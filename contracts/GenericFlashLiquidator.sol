// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title TONI generic Aave-flash liquidator (Spark / Compound III / Morpho Blue / Aave V3)
/// @notice Flash from Aave V3 Pool (~9 bps), liquidate the target venue, Uni V3 swap coll→flash asset, repay.
/// No constructor args. Mainnet Aave V3 / Morpho / Uni addresses are constants.
/// After deploy: set LIQ_GENERIC_CONTRACT (and optionally LIQ_CONTRACT) to this address.
/// Then `cast send <addr> "setOwner(address)" <botEOA>` if you did not deploy from the bot keystore.
/// `flashLiquidate(address,address,address,uint256)` stays ABI-compatible with the old Aave-only executor.

interface IAavePool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool receiveAToken
    ) external;
}

interface IUniswapV3Factory {
    function getPool(address tokenA, address tokenB, uint24 fee) external view returns (address);
}

interface ISwapRouter02 {
    struct ExactInputParams {
        bytes path;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }
    function exactInput(ExactInputParams calldata params) external payable returns (uint256);
}

interface IComet {
    function absorb(address absorber, address[] calldata accounts) external;
    function buyCollateral(address asset, uint256 minAmount, uint256 baseAmount, address recipient) external;
    function quoteCollateral(address asset, uint256 baseAmount) external view returns (uint256);
    function getCollateralReserves(address asset) external view returns (uint256);
    function baseToken() external view returns (address);
}

interface IMorpho {
    struct MarketParams {
        address loanToken;
        address collateralToken;
        address oracle;
        address irm;
        uint256 lltv;
    }
    function liquidate(
        MarketParams calldata marketParams,
        address borrower,
        uint256 seizedAssets,
        uint256 repaidShares,
        bytes calldata data
    ) external returns (uint256, uint256);
}

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

contract GenericFlashLiquidator {
    bytes32 public constant KIND = keccak256("toni.genericFlashLiq.v1");

    address public constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address public constant MORPHO = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
    address public constant SWAP_ROUTER = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;
    address public constant UNI_FACTORY = 0x1F98431c8aD98523631AE4a59f267346ea31F984;
    address public constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;

    uint8 internal constant V_AAVE_LIKE = 1;
    uint8 internal constant V_COMET = 2;
    uint8 internal constant V_MORPHO = 3;

    address public owner;

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "owner");
        _;
    }

    receive() external payable {}

    function setOwner(address n) external onlyOwner {
        require(n != address(0), "zero");
        owner = n;
    }

    /// Pull leftover ERC20. `amount == 0` sends the full token balance to owner.
    function recover(address token, uint256 amount) external onlyOwner {
        uint256 amt = amount == 0 ? IERC20(token).balanceOf(address(this)) : amount;
        _push(token, owner, amt);
    }

    function recoverEth() external onlyOwner {
        uint256 amt = address(this).balance;
        require(amt > 0, "eth");
        (bool ok, ) = owner.call{value: amt}("");
        require(ok, "eth");
    }

    /// Aave V3-only 4-arg (same signature as the previous LIQ_CONTRACT).
    function flashLiquidate(address collateral, address debt, address user, uint256 debtToCover) external {
        uint256 amt = _aaveLikeFlashAmt(AAVE_POOL, debt, user, debtToCover);
        bytes memory payload = abi.encode(
            V_AAVE_LIKE, AAVE_POOL, user, collateral, debt, amt,
            address(0), address(0), uint256(0), uint256(0), uint256(0), bytes("")
        );
        IAavePool(AAVE_POOL).flashLoanSimple(address(this), debt, amt, payload, 0);
    }

    /// Aave V3-shaped pool (Aave or Spark). Flash still comes from Aave V3.
    function flashLiquidatePool(
        address pool,
        address collateral,
        address debt,
        address user,
        uint256 debtToCover,
        bytes calldata swapPath
    ) external {
        uint256 amt = _aaveLikeFlashAmt(pool, debt, user, debtToCover);
        bytes memory payload = abi.encode(
            V_AAVE_LIKE, pool, user, collateral, debt, amt,
            address(0), address(0), uint256(0), uint256(0), uint256(0), swapPath
        );
        IAavePool(AAVE_POOL).flashLoanSimple(address(this), debt, amt, payload, 0);
    }

    /// Compound III: absorb (if needed) + buyCollateral, then swap coll → base.
    function flashLiquidateComet(
        address comet,
        address user,
        address collateral,
        uint256 baseAmount,
        bytes calldata swapPath
    ) external {
        require(baseAmount > 0 && baseAmount < type(uint256).max, "amt");
        address base = IComet(comet).baseToken();
        bytes memory payload = abi.encode(
            V_COMET, comet, user, collateral, base, baseAmount,
            address(0), address(0), uint256(0), uint256(0), uint256(0), swapPath
        );
        IAavePool(AAVE_POOL).flashLoanSimple(address(this), base, baseAmount, payload, 0);
    }

    /// Morpho Blue `liquidate`. One of seizedAssets / repaidShares must be 0 (Morpho rule).
    /// `flashAmount` is loan-token wei borrowed from Aave V3 (must cover Morpho pull + 9 bps).
    function flashLiquidateMorpho(
        address loanToken,
        address collToken,
        address oracle,
        address irm,
        uint256 lltv,
        address user,
        uint256 seizedAssets,
        uint256 repaidShares,
        uint256 flashAmount,
        bytes calldata swapPath
    ) external {
        require(flashAmount > 0 && flashAmount < type(uint256).max, "amt");
        require(seizedAssets == 0 || repaidShares == 0, "morpho side");
        bytes memory payload = abi.encode(
            V_MORPHO, MORPHO, user, collToken, loanToken, flashAmount,
            oracle, irm, lltv, seizedAssets, repaidShares, swapPath
        );
        IAavePool(AAVE_POOL).flashLoanSimple(address(this), loanToken, flashAmount, payload, 0);
    }

    struct Job {
        uint8 venue;
        address target;
        address user;
        address coll;
        address debt;
        uint256 cover;
        address oracle;
        address irm;
        uint256 lltv;
        uint256 seized;
        uint256 repaidShares;
        bytes path;
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        require(msg.sender == AAVE_POOL, "pool");
        require(initiator == address(this), "init");
        Job memory job = abi.decode(params, (Job));
        require(asset == job.debt, "asset");
        require(job.cover > 0, "cover");

        if (job.venue == V_AAVE_LIKE) {
            _approve(job.debt, job.target, job.cover);
            IAavePool(job.target).liquidationCall(job.coll, job.debt, job.user, job.cover, false);
        } else if (job.venue == V_COMET) {
            _cometAbsorbBuy(IComet(job.target), job.user, job.coll, job.cover);
        } else if (job.venue == V_MORPHO) {
            _morphoLiq(job.user, job.coll, job.debt, job.oracle, job.irm, job.lltv, job.seized, job.repaidShares, job.cover);
        } else {
            revert("venue");
        }

        _swapAll(job.coll, job.debt, job.path);

        uint256 repay = amount + premium;
        uint256 bal = IERC20(asset).balanceOf(address(this));
        require(bal >= repay, "unprofitable");
        uint256 profit = bal - repay;
        if (profit > 0) {
            _push(asset, owner, profit);
        }
        _approve(asset, AAVE_POOL, repay);
        return true;
    }

    function _aaveLikeFlashAmt(address pool, address debt, address user, uint256 debtToCover)
        internal
        view
        returns (uint256)
    {
        uint256 owed = _variableStableDebt(pool, debt, user);
        require(owed > 0, "debt");
        uint256 cf = _closeFactorBps(pool, user);
        uint256 cap = owed * cf / 10_000;
        if (debtToCover == 0 || debtToCover == type(uint256).max) return cap;
        return debtToCover < cap ? debtToCover : cap;
    }

    function _variableStableDebt(address pool, address asset, address user) internal view returns (uint256) {
        (bool ok, bytes memory data) = pool.staticcall(abi.encodeWithSelector(0x35ea6a75, asset)); // getReserveData
        require(ok && data.length >= 352, "reserve");
        address stable;
        address variable;
        assembly {
            stable := mload(add(data, 320))
            variable := mload(add(data, 352))
        }
        uint256 tot;
        if (variable != address(0)) tot += IERC20(variable).balanceOf(user);
        if (stable != address(0)) tot += IERC20(stable).balanceOf(user);
        return tot;
    }

    function _closeFactorBps(address pool, address user) internal view returns (uint256) {
        (bool ok, bytes memory data) = pool.staticcall(abi.encodeWithSelector(0xbf92857c, user)); // getUserAccountData
        require(ok && data.length >= 192, "acct");
        uint256 hf;
        assembly { hf := mload(add(data, 192)) }
        if (hf < 0.95e18) return 10_000;
        return 5_000;
    }

    function _cometAbsorbBuy(IComet comet, address user, address coll, uint256 baseAmount) internal {
        address[] memory accts = new address[](1);
        accts[0] = user;
        try comet.absorb(address(this), accts) {} catch {}
        uint256 reserves = comet.getCollateralReserves(coll);
        require(reserves > 0, "comet reserves");
        uint256 spend = baseAmount;
        if (comet.quoteCollateral(coll, spend) > reserves) {
            uint256 lo = 0;
            uint256 hi = spend;
            for (uint256 i = 0; i < 14; i++) {
                uint256 mid = (lo + hi) / 2;
                if (mid == lo) break;
                if (comet.quoteCollateral(coll, mid) <= reserves) lo = mid;
                else hi = mid;
            }
            spend = lo;
        }
        require(spend > 0, "comet spend");
        address base = comet.baseToken();
        _approve(base, address(comet), spend);
        comet.buyCollateral(coll, 1, spend, address(this));
    }

    function _morphoLiq(
        address user,
        address coll,
        address loan,
        address oracle,
        address irm,
        uint256 lltv,
        uint256 seized,
        uint256 repaidShares,
        uint256 flashAmt
    ) internal {
        IMorpho.MarketParams memory mp = IMorpho.MarketParams({
            loanToken: loan,
            collateralToken: coll,
            oracle: oracle,
            irm: irm,
            lltv: lltv
        });
        _approve(loan, MORPHO, flashAmt);
        IMorpho(MORPHO).liquidate(mp, user, seized, repaidShares, "");
    }

    function _swapAll(address tokenIn, address tokenOut, bytes memory path) internal {
        if (tokenIn == tokenOut) return;
        uint256 amt = IERC20(tokenIn).balanceOf(address(this));
        if (amt == 0) return;
        if (path.length < 43) {
            path = _bestPath(tokenIn, tokenOut);
        }
        require(path.length >= 43, "path");
        _approve(tokenIn, SWAP_ROUTER, amt);
        ISwapRouter02(SWAP_ROUTER).exactInput(
            ISwapRouter02.ExactInputParams({
                path: path,
                recipient: address(this),
                amountIn: amt,
                amountOutMinimum: 1
            })
        );
    }

    function _bestPath(address tokenIn, address tokenOut) internal view returns (bytes memory) {
        uint24[4] memory fees = [uint24(500), uint24(3000), uint24(100), uint24(10000)];
        for (uint256 i = 0; i < 4; i++) {
            if (_pool(tokenIn, tokenOut, fees[i]) != address(0)) {
                return abi.encodePacked(tokenIn, fees[i], tokenOut);
            }
        }
        if (tokenIn != WETH && tokenOut != WETH) {
            uint24 f1;
            uint24 f2;
            bool a;
            bool b;
            for (uint256 i = 0; i < 4; i++) {
                if (_pool(tokenIn, WETH, fees[i]) != address(0)) { f1 = fees[i]; a = true; break; }
            }
            for (uint256 i = 0; i < 4; i++) {
                if (_pool(WETH, tokenOut, fees[i]) != address(0)) { f2 = fees[i]; b = true; break; }
            }
            if (a && b) {
                return abi.encodePacked(tokenIn, f1, WETH, f2, tokenOut);
            }
        }
        return bytes("");
    }

    function _pool(address a, address b, uint24 fee) internal view returns (address p) {
        p = IUniswapV3Factory(UNI_FACTORY).getPool(a, b, fee);
    }

    function _approve(address token, address spender, uint256 amount) internal {
        (bool ok0, ) = token.call(abi.encodeWithSelector(0x095ea7b3, spender, uint256(0)));
        ok0;
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(0x095ea7b3, spender, amount));
        require(ok && (ret.length == 0 || abi.decode(ret, (bool))), "approve");
    }

    function _push(address token, address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(0xa9059cbb, to, amount));
        require(ok && (ret.length == 0 || abi.decode(ret, (bool))), "xfer");
    }
}
