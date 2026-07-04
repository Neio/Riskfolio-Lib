import os
import datetime
import numpy as np
import pandas as pd
import riskfolio as rp
import matplotlib
# Use non-interactive plotting
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# -------------------------------------------------------------
# TAX CONFIGURATION (Highest bracket for California Resident)
# -------------------------------------------------------------
STCG_TAX_RATE = 0.552
LTCG_TAX_RATE = 0.382
FEE_RATE = 0.001

def run_mixed_tax_backtest():
    print("--- Starting Mixed Stock Universe CA Tax-Aware DCA Backtest ---")
    print(f"STCG Tax Rate: {STCG_TAX_RATE*100:.1f}% | LTCG Tax Rate: {LTCG_TAX_RATE*100:.1f}%")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(script_dir, "cached_prices")
    
    # Mixed stock list
    stock_tickers = ['NVDA', 'PG', 'KO', 'WMT', 'INTC', 'PFE', 'PYPL']
    all_tickers = stock_tickers + ['QQQ']
    
    # Load cached prices
    prices_dict = {}
    for ticker in all_tickers:
        ticker_file = os.path.join(cache_dir, f"{ticker}.csv")
        if os.path.exists(ticker_file):
            df = pd.read_csv(ticker_file, parse_dates=True, index_col=0)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            prices_dict[ticker] = df.iloc[:, 0]
            
    prices_df = pd.DataFrame(prices_dict).dropna().sort_index()
    returns_df = prices_df[stock_tickers].pct_change().dropna()
    qqq_returns = prices_df['QQQ'].pct_change().dropna()
    n_days = len(returns_df)
    
    # DCA setup
    start_idx = 504
    rebalance_freq = 21
    dca_amount = 2000.0
    
    taxable_std_value = []
    taxable_buy_only_value = []
    tax_free_value = []
    cash_savings_value = []
    principal_value = []
    
    taxable_std_lots = {t: [] for t in stock_tickers}
    taxable_buy_only_lots = {t: [] for t in stock_tickers}
    
    tax_free_holdings = pd.Series(0.0, index=stock_tickers)
    cash_holdings = 0.0
    qqq_holdings = 0.0
    principal_invested = 0.0
    principal_value = []
    
    total_tax_paid_std = 0.0
    
    print(f"\nSimulating Mixed DCA of ${dca_amount:,.2f} monthly from {returns_df.index[start_idx].date()} to {returns_df.index[-1].date()}...")
    
    for i in range(start_idx, n_days):
        current_date = returns_df.index[i]
        daily_ret = returns_df.iloc[i]
        qqq_daily_ret = qqq_returns.iloc[i]
        daily_prices = prices_df.iloc[i]
        
        # A. Update holdings based on daily price drift
        tax_free_holdings = tax_free_holdings * (1.0 + daily_ret)
        cash_holdings = cash_holdings * (1.0 + 0.04 / 252)
        qqq_holdings = qqq_holdings * (1.0 + qqq_daily_ret)
        
        # B. Check if today is deposit & rebalancing day
        if (i - start_idx) % rebalance_freq == 0:
            cash_holdings += dca_amount
            qqq_holdings += dca_amount
            principal_invested += dca_amount
            
            # --- Get Optimal Sharpe Weights using 1-year historical lookback ---
            lookback_data = returns_df.iloc[i - 252:i]
            try:
                port = rp.Portfolio(returns=lookback_data)
                port.assets_stats(method_mu='hist', method_cov='hist')
                port.solvers = ['CLARABEL', 'SCS']
                w_opt = port.optimization(model='Classic', rm='MV', obj='Sharpe', rf=0.04/252, hist=True)
                opt_target_weights = w_opt['weights'].iloc[:, 0]
            except Exception as e:
                # Fallback to equal weighting if optimization fails
                opt_target_weights = pd.Series(1.0 / len(stock_tickers), index=stock_tickers)
                
            # -----------------------------------------------------------------
            # STRATEGY 1: Standard Rebalance (Taxable)
            # -----------------------------------------------------------------
            std_current_val = sum(sum(lot['shares'] * daily_prices[t] for lot in taxable_std_lots[t]) for t in stock_tickers)
            std_total_before = std_current_val + dca_amount
            
            std_target_dollars = opt_target_weights * std_total_before
            std_current_values = pd.Series({t: sum(lot['shares'] * daily_prices[t] for lot in taxable_std_lots[t]) for t in stock_tickers})
            std_diffs = std_target_dollars - std_current_values
            
            sells_cash = 0.0
            sells_fee = 0.0
            sells_tax = 0.0
            
            for t in stock_tickers:
                diff = std_diffs[t]
                if diff < 0:  # SELL needed
                    sell_value_needed = -diff
                    sells_fee += sell_value_needed * FEE_RATE
                    
                    lots = taxable_std_lots[t]
                    lots.sort(key=lambda x: x['buy_price'], reverse=True)
                    
                    shares_to_sell = sell_value_needed / daily_prices[t]
                    sold_shares = 0.0
                    
                    while shares_to_sell > 0 and len(lots) > 0:
                        lot = lots[0]
                        shares_from_lot = min(shares_to_sell, lot['shares'])
                        
                        cost_basis = shares_from_lot * lot['buy_price']
                        sale_value = shares_from_lot * daily_prices[t]
                        gain = sale_value - cost_basis
                        
                        age = (current_date - lot['date']).days
                        is_long_term = age > 365
                        tax_rate = LTCG_TAX_RATE if is_long_term else STCG_TAX_RATE
                        
                        lot_tax = gain * tax_rate
                        sells_tax += lot_tax
                        
                        lot['shares'] -= shares_from_lot
                        shares_to_sell -= shares_from_lot
                        sold_shares += shares_from_lot
                        
                        if lot['shares'] <= 0.0001:
                            lots.pop(0)
                            
                    sells_cash += (sold_shares * daily_prices[t])
            
            total_tax_paid_std += max(0.0, sells_tax)
            buy_budget = dca_amount + sells_cash - sells_fee - sells_tax
            
            buys_total_needed = std_diffs[std_diffs > 0].sum()
            for t in stock_tickers:
                diff = std_diffs[t]
                if diff > 0 and buys_total_needed > 0:
                    ratio = diff / buys_total_needed
                    buy_value = ratio * buy_budget
                    
                    net_buy_value = buy_value * (1.0 - FEE_RATE)
                    buy_shares = net_buy_value / daily_prices[t]
                    
                    taxable_std_lots[t].append({
                        'date': current_date,
                        'shares': buy_shares,
                        'buy_price': daily_prices[t]
                    })
                    
            # -----------------------------------------------------------------
            # STRATEGY 2: Buy-Only Rebalance (Taxable)
            # -----------------------------------------------------------------
            buy_only_current_val = sum(sum(lot['shares'] * daily_prices[t] for lot in taxable_buy_only_lots[t]) for t in stock_tickers)
            buy_only_total_before = buy_only_current_val + dca_amount
            
            buy_only_target_dollars = opt_target_weights * buy_only_total_before
            buy_only_current_values = pd.Series({t: sum(lot['shares'] * daily_prices[t] for lot in taxable_buy_only_lots[t]) for t in stock_tickers})
            
            buy_only_diffs = buy_only_target_dollars - buy_only_current_values
            underallocated_diffs = buy_only_diffs[buy_only_diffs > 0]
            
            buy_only_budget = dca_amount
            total_underallocated = underallocated_diffs.sum()
            
            for t in stock_tickers:
                if t in underallocated_diffs.index and total_underallocated > 0:
                    ratio = underallocated_diffs[t] / total_underallocated
                    buy_value = ratio * buy_only_budget
                    
                    net_buy_value = buy_value * (1.0 - FEE_RATE)
                    buy_shares = net_buy_value / daily_prices[t]
                    
                    taxable_buy_only_lots[t].append({
                        'date': current_date,
                        'shares': buy_shares,
                        'buy_price': daily_prices[t]
                    })
            
            # -----------------------------------------------------------------
            # STRATEGY 3: Standard Rebalance (Tax-Free)
            # -----------------------------------------------------------------
            tax_free_total_before = tax_free_holdings.sum() + dca_amount
            tax_free_target_dollars = opt_target_weights * tax_free_total_before
            
            tax_free_turnover = np.sum(np.abs(tax_free_target_dollars - tax_free_holdings))
            tax_free_fee = tax_free_turnover * FEE_RATE
            
            tax_free_holdings = opt_target_weights * (tax_free_total_before - tax_free_fee)
            
        # C. Record daily values
        current_val_std = sum(sum(lot['shares'] * daily_prices[t] for lot in taxable_std_lots[t]) for t in stock_tickers)
        taxable_std_value.append(current_val_std)
        
        current_val_buy_only = sum(sum(lot['shares'] * daily_prices[t] for lot in taxable_buy_only_lots[t]) for t in stock_tickers)
        taxable_buy_only_value.append(current_val_buy_only)
        
        tax_free_value.append(tax_free_holdings.sum())
        cash_savings_value.append(cash_holdings)
        principal_value.append(principal_invested)
        
    backtest_dates = returns_df.index[start_idx:]
    results_df = pd.DataFrame(index=backtest_dates)
    results_df['Optimized Portfolio (Tax-Free)'] = tax_free_value
    results_df['Optimized Portfolio (Taxable Std - 55.2% STCG)'] = taxable_std_value
    results_df['Optimized Portfolio (Taxable Buy-Only)'] = taxable_buy_only_value
    results_df['Cash Savings (4% Yield)'] = cash_savings_value
    results_df['Cash Savings (Principal)'] = principal_value
    
    # Calculate performance metrics
    metrics = {}
    for col in results_df.columns:
        final_value = results_df[col].iloc[-1]
        total_invested = results_df['Cash Savings (Principal)'].iloc[-1]
        net_profit = final_value - total_invested
        profit_pct = (net_profit / total_invested) * 100
        
        # Max Drawdown
        cum_max = results_df[col].cummax()
        drawdown = (results_df[col] - cum_max) / cum_max
        max_dd = drawdown.min()
        
        metrics[col] = {
            'Total Invested ($)': f"${total_invested:,.2f}",
            'Final Portfolio Value ($)': f"${final_value:,.2f}",
            'Net Profit/Loss ($)': f"${net_profit:,.2f}",
            'Total Profit (%)': f"{profit_pct:+.2f}%",
            'Max Drawdown (%)': f"{max_dd * 100:.2f}%"
        }
        
    metrics_df = pd.DataFrame(metrics)
    
    print("\n--- CA Tax-Aware Mixed Universe DCA Summary ---")
    print("-------------------------------------------------")
    for idx, row in metrics_df.T.iterrows():
        print(f"{idx:<50s} | Invested: {row['Total Invested ($)'] :>11s} | Final: {row['Final Portfolio Value ($)'] :>11s} | Net: {row['Net Profit/Loss ($)'] :>10s} | Profit: {row['Total Profit (%)'] :>8s} | MaxDD: {row['Max Drawdown (%)'] :>7s}")
    print(f"\nTotal Capital Gains Tax Paid (Standard Rebalance): ${total_tax_paid_std:,.2f}")
    
    # Plot performance (% ROI)
    plt.figure(figsize=(12, 6))
    
    roi_free = (results_df['Optimized Portfolio (Tax-Free)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    roi_std = (results_df['Optimized Portfolio (Taxable Std - 55.2% STCG)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    roi_buy_only = (results_df['Optimized Portfolio (Taxable Buy-Only)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    roi_cash = (results_df['Cash Savings (4% Yield)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    
    plt.plot(results_df.index, roi_free, label='Optimized Portfolio (Tax-Free / IRA)', color='blue', linewidth=2.5)
    plt.plot(results_df.index, roi_buy_only, label='Optimized Portfolio (Taxable Buy-Only / 0% Tax)', color='orange', linewidth=2)
    plt.plot(results_df.index, roi_std, label='Optimized Portfolio (Taxable Std - 55.2% Max CA Tax)', color='red', linewidth=1.5, linestyle='-.')
    plt.plot(results_df.index, roi_cash, label='Cash Savings (4% Yield)', color='purple', linewidth=1.5, linestyle=':')
    plt.axhline(0, label='Cash Principal (0% Return Baseline)', color='gray', linewidth=1.5, linestyle='--')
    
    plt.title('Monthly DCA Simulation ($2,000/Month): Mixed Universe % ROI Comparison (CA Tax)', fontsize=14, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Return on Invested Capital (%)', fontsize=12)
    plt.legend(fontsize=10)
    plt.grid(linestyle=':', alpha=0.6)
    
    plt.gcf().autofmt_xdate()
    
    output_path = os.path.join(script_dir, "mixed_tax_aware_performance.png")
    plt.savefig(output_path, bbox_inches='tight')
    plt.close()
    
    print(f"\nMixed tax-aware performance chart saved to: {output_path}")

if __name__ == "__main__":
    run_mixed_tax_backtest()
