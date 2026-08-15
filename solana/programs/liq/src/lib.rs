//! TONI `liq` — Solana liquidation plan executor (scaffold).
//!
//! Target protocol: Solend (Save). Instructions are compile-ready stubs:
//! full Solend CPI + flash repay/seize needs the live Solend SDK accounts
//! wired in a follow-up. Do not treat placeholder program IDs as deployed.
//!
//! Account layout for future CPI (remaining_accounts order):
//!   0 obligation, 1 repay_reserve, 2 withdraw_reserve,
//!   3 repay_source_liquidity, 4 withdraw_destination_collateral,
//!   5 lending_market, 6 lending_market_authority, 7 solend_program

use anchor_lang::prelude::*;

declare_id!("Liq1111111111111111111111111111111111111112");

/// Minimum profit gate (USD scaled 1e6) — dashboard mirrors via MIN_LIQ_PROFIT_USD.
pub const MIN_PROFIT_USD_MICROS: u64 = 10_000_000; // $10

#[program]
pub mod liq {
    use super::*;

    /// Initialize executor config PDA (authority + min profit + paused).
    pub fn initialize(ctx: Context<Initialize>, min_profit_usd_micros: u64) -> Result<()> {
        let cfg = &mut ctx.accounts.config;
        cfg.authority = ctx.accounts.authority.key();
        cfg.min_profit_usd_micros = if min_profit_usd_micros == 0 {
            MIN_PROFIT_USD_MICROS
        } else {
            min_profit_usd_micros
        };
        cfg.paused = false;
        cfg.bump = ctx.bumps.config;
        cfg.stats_executed = 0;
        cfg.stats_simulated = 0;
        cfg.solend_program = Pubkey::default(); // set via set_solend_program
        msg!(
            "liq:init authority={} min_profit_micros={}",
            cfg.authority,
            cfg.min_profit_usd_micros
        );
        Ok(())
    }

    /// Record Solend program id once known (mainnet So1endD… or Save fork).
    pub fn set_solend_program(ctx: Context<SetSolendProgram>, program_id: Pubkey) -> Result<()> {
        require!(
            ctx.accounts.authority.key() == ctx.accounts.config.authority,
            LiqError::BadAuthority
        );
        ctx.accounts.config.solend_program = program_id;
        msg!("liq:solend_program={}", program_id);
        Ok(())
    }

    /// Simulate a liquidation plan without CPI (dashboard sim-only / dry-run).
    pub fn simulate_plan(ctx: Context<SimulatePlan>, plan: LiqPlan) -> Result<()> {
        require!(!ctx.accounts.config.paused, LiqError::Paused);
        require!(plan.debt_amount > 0, LiqError::BadAmount);
        require!(
            plan.expected_profit_usd_micros >= ctx.accounts.config.min_profit_usd_micros,
            LiqError::BelowMinProfit
        );
        ctx.accounts.config.stats_simulated = ctx
            .accounts
            .config
            .stats_simulated
            .checked_add(1)
            .ok_or(LiqError::Overflow)?;
        emit!(LiqSimulated {
            obligation: plan.obligation,
            repay_mint: plan.repay_mint,
            withdraw_mint: plan.withdraw_mint,
            debt_amount: plan.debt_amount,
            profit_usd_micros: plan.expected_profit_usd_micros,
            jito_tip_lamports: plan.jito_tip_lamports,
        });
        msg!(
            "liq:sim obligation={} debt={} profit_micros={} jito_tip={}",
            plan.obligation,
            plan.debt_amount,
            plan.expected_profit_usd_micros,
            plan.jito_tip_lamports
        );
        Ok(())
    }

    /// Execute plan stub — account metas reserved for future Solend CPI.
    /// Currently records intent + emits event; does NOT move funds.
    pub fn execute_plan(ctx: Context<ExecutePlan>, plan: LiqPlan) -> Result<()> {
        require!(!ctx.accounts.config.paused, LiqError::Paused);
        require!(
            ctx.accounts.authority.key() == ctx.accounts.config.authority,
            LiqError::BadAuthority
        );
        require!(plan.debt_amount > 0, LiqError::BadAmount);
        require!(
            plan.expected_profit_usd_micros >= ctx.accounts.config.min_profit_usd_micros,
            LiqError::BelowMinProfit
        );

        // TODO: Solend `liquidate_obligation_and_redeem_reserve_collateral` CPI
        // Remaining accounts: see module docs (obligation → solend_program).
        let _remaining = &ctx.remaining_accounts;
        let _ = ctx.accounts.config.solend_program;

        ctx.accounts.config.stats_executed = ctx
            .accounts
            .config
            .stats_executed
            .checked_add(1)
            .ok_or(LiqError::Overflow)?;

        emit!(LiqExecuted {
            obligation: plan.obligation,
            repay_mint: plan.repay_mint,
            withdraw_mint: plan.withdraw_mint,
            debt_amount: plan.debt_amount,
            profit_usd_micros: plan.expected_profit_usd_micros,
            jito_tip_lamports: plan.jito_tip_lamports,
        });
        msg!("liq:execute stub — wire Solend CPI before mainnet arm");
        Ok(())
    }

    pub fn set_paused(ctx: Context<SetPaused>, paused: bool) -> Result<()> {
        require!(
            ctx.accounts.authority.key() == ctx.accounts.config.authority,
            LiqError::BadAuthority
        );
        ctx.accounts.config.paused = paused;
        Ok(())
    }
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Debug)]
pub struct LiqPlan {
    pub obligation: Pubkey,
    pub repay_mint: Pubkey,
    pub withdraw_mint: Pubkey,
    pub debt_amount: u64,
    pub expected_profit_usd_micros: u64,
    /// Optional Jito tip (lamports) — metadata for bundle builders; not transferred here.
    pub jito_tip_lamports: u64,
}

#[account]
pub struct Config {
    pub authority: Pubkey,
    pub min_profit_usd_micros: u64,
    pub paused: bool,
    pub bump: u8,
    pub stats_executed: u64,
    pub stats_simulated: u64,
    pub solend_program: Pubkey,
}

impl Config {
    pub const LEN: usize = 8 + 32 + 8 + 1 + 1 + 8 + 8 + 32;
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,
    #[account(
        init,
        payer = authority,
        space = Config::LEN,
        seeds = [b"liq-config"],
        bump
    )]
    pub config: Account<'info, Config>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SetSolendProgram<'info> {
    pub authority: Signer<'info>,
    #[account(mut, seeds = [b"liq-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[derive(Accounts)]
pub struct SimulatePlan<'info> {
    #[account(mut, seeds = [b"liq-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[derive(Accounts)]
pub struct ExecutePlan<'info> {
    pub authority: Signer<'info>,
    #[account(mut, seeds = [b"liq-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[derive(Accounts)]
pub struct SetPaused<'info> {
    pub authority: Signer<'info>,
    #[account(mut, seeds = [b"liq-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[event]
pub struct LiqSimulated {
    pub obligation: Pubkey,
    pub repay_mint: Pubkey,
    pub withdraw_mint: Pubkey,
    pub debt_amount: u64,
    pub profit_usd_micros: u64,
    pub jito_tip_lamports: u64,
}

#[event]
pub struct LiqExecuted {
    pub obligation: Pubkey,
    pub repay_mint: Pubkey,
    pub withdraw_mint: Pubkey,
    pub debt_amount: u64,
    pub profit_usd_micros: u64,
    pub jito_tip_lamports: u64,
}

#[error_code]
pub enum LiqError {
    #[msg("Program paused")]
    Paused,
    #[msg("Bad authority")]
    BadAuthority,
    #[msg("Debt amount must be > 0")]
    BadAmount,
    #[msg("Expected profit below configured minimum")]
    BelowMinProfit,
    #[msg("Arithmetic overflow")]
    Overflow,
}
