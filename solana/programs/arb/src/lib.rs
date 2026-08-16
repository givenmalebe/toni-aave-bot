//! TONI `arb` — DEX round-trip executor (Jupiter CPI placeholder).
//!
//! Live TONI send path is Python: Jupiter `/swap` serialized tx + Jito bundle
//! (`sol_scanner.submit_sol_plan`). This on-chain program is a compile-ready
//! stub only — `execute_roundtrip` does **not** CPI into Jupiter. Never deploy
//! or send to the placeholder `Arb1111…` id (no-op would burn CU).
//!
//! Remaining accounts if CPI is wired later:
//!   Jupiter route account metas from `/swap` serialized transaction,
//!   plus optional Jito tip account.

use anchor_lang::prelude::*;

declare_id!("Arb1111111111111111111111111111111111111112");

pub const MIN_PROFIT_USD_MICROS: u64 = 50_000; // $0.05 default (dashboard MIN_SOL_ARB_USD)

#[program]
pub mod arb {
    use super::*;

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
        cfg.stats_simulated = 0;
        cfg.stats_executed = 0;
        Ok(())
    }

    /// Dry-run a multi-hop round-trip plan (no CPI). Matches dashboard build_arb_plan.
    pub fn simulate_roundtrip(ctx: Context<SimulateRoundtrip>, plan: ArbPlan) -> Result<()> {
        require!(!ctx.accounts.config.paused, ArbError::Paused);
        require!(plan.amount_in > 0, ArbError::BadAmount);
        require!(
            plan.expected_profit_usd_micros >= ctx.accounts.config.min_profit_usd_micros,
            ArbError::BelowMinProfit
        );
        ctx.accounts.config.stats_simulated = ctx
            .accounts
            .config
            .stats_simulated
            .checked_add(1)
            .ok_or(ArbError::Overflow)?;
        emit!(ArbSimulated {
            input_mint: plan.input_mint,
            mid_mint: plan.mid_mint,
            amount_in: plan.amount_in,
            min_amount_out: plan.min_amount_out,
            profit_usd_micros: plan.expected_profit_usd_micros,
            jito_tip_lamports: plan.jito_tip_lamports,
            priority_fee_ul: plan.priority_fee_ul,
        });
        msg!(
            "arb:sim in={} mid={} out_min={} profit_micros={} tip={}",
            plan.amount_in,
            plan.mid_mint,
            plan.min_amount_out,
            plan.expected_profit_usd_micros,
            plan.jito_tip_lamports
        );
        Ok(())
    }

    /// Execute stub — remaining accounts reserved for Jupiter route CPI.
    pub fn execute_roundtrip(ctx: Context<ExecuteRoundtrip>, plan: ArbPlan) -> Result<()> {
        require!(!ctx.accounts.config.paused, ArbError::Paused);
        require!(
            ctx.accounts.authority.key() == ctx.accounts.config.authority,
            ArbError::BadAuthority
        );
        require!(plan.amount_in > 0, ArbError::BadAmount);
        require!(
            plan.expected_profit_usd_micros >= ctx.accounts.config.min_profit_usd_micros,
            ArbError::BelowMinProfit
        );

        // TODO: CPI into Jupiter aggregator with serialized route plan
        let _remaining = &ctx.remaining_accounts;

        ctx.accounts.config.stats_executed = ctx
            .accounts
            .config
            .stats_executed
            .checked_add(1)
            .ok_or(ArbError::Overflow)?;

        emit!(ArbExecuted {
            input_mint: plan.input_mint,
            mid_mint: plan.mid_mint,
            amount_in: plan.amount_in,
            min_amount_out: plan.min_amount_out,
            profit_usd_micros: plan.expected_profit_usd_micros,
            jito_tip_lamports: plan.jito_tip_lamports,
            priority_fee_ul: plan.priority_fee_ul,
        });
        msg!("arb:execute stub — dashboard live path is Python Jupiter+Jito, not this CPI");
        Ok(())
    }

    pub fn set_paused(ctx: Context<SetPaused>, paused: bool) -> Result<()> {
        require!(
            ctx.accounts.authority.key() == ctx.accounts.config.authority,
            ArbError::BadAuthority
        );
        ctx.accounts.config.paused = paused;
        Ok(())
    }
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Debug)]
pub struct ArbPlan {
    pub input_mint: Pubkey,
    /// Hop mint (USDC / mSOL / …) for A→B→A round-trips.
    pub mid_mint: Pubkey,
    pub amount_in: u64,
    pub min_amount_out: u64,
    pub expected_profit_usd_micros: u64,
    /// Optional Jito tip (lamports) — plan metadata only in this stub.
    pub jito_tip_lamports: u64,
    /// Priority fee sample (microlamports/CU) used when sizing net profit.
    pub priority_fee_ul: u64,
}

#[account]
pub struct Config {
    pub authority: Pubkey,
    pub min_profit_usd_micros: u64,
    pub paused: bool,
    pub bump: u8,
    pub stats_executed: u64,
    pub stats_simulated: u64,
}

impl Config {
    pub const LEN: usize = 8 + 32 + 8 + 1 + 1 + 8 + 8;
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,
    #[account(
        init,
        payer = authority,
        space = Config::LEN,
        seeds = [b"arb-config"],
        bump
    )]
    pub config: Account<'info, Config>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SimulateRoundtrip<'info> {
    #[account(mut, seeds = [b"arb-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[derive(Accounts)]
pub struct ExecuteRoundtrip<'info> {
    pub authority: Signer<'info>,
    #[account(mut, seeds = [b"arb-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[derive(Accounts)]
pub struct SetPaused<'info> {
    pub authority: Signer<'info>,
    #[account(mut, seeds = [b"arb-config"], bump = config.bump)]
    pub config: Account<'info, Config>,
}

#[event]
pub struct ArbSimulated {
    pub input_mint: Pubkey,
    pub mid_mint: Pubkey,
    pub amount_in: u64,
    pub min_amount_out: u64,
    pub profit_usd_micros: u64,
    pub jito_tip_lamports: u64,
    pub priority_fee_ul: u64,
}

#[event]
pub struct ArbExecuted {
    pub input_mint: Pubkey,
    pub mid_mint: Pubkey,
    pub amount_in: u64,
    pub min_amount_out: u64,
    pub profit_usd_micros: u64,
    pub jito_tip_lamports: u64,
    pub priority_fee_ul: u64,
}

#[error_code]
pub enum ArbError {
    #[msg("Program paused")]
    Paused,
    #[msg("Bad authority")]
    BadAuthority,
    #[msg("Amount must be > 0")]
    BadAmount,
    #[msg("Expected profit below configured minimum")]
    BelowMinProfit,
    #[msg("Arithmetic overflow")]
    Overflow,
}
