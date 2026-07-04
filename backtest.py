import os
import datetime
import numpy as np
import pandas as pd
import riskfolio as rp
import matplotlib
# Use a non-interactive backend for saving plots
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def run_backtest():
    print("--- Starting Riskfolio-Lib Rolling Portfolio Backtest ---")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(script_dir, "cached_prices")
    
    # 1. Load cached price data
    tickers = ['TSLA', 'NVDA', 'PANW', 'MU', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']
    prices_dict = {}
    
    for ticker in tickers:
        file_path = os.path.join(cache_dir, f"{ticker}.csv")
        if os.path.exists(file_path):
            series = pd.read_csv(file_path, parse_dates=True, index_col=0).iloc[:, 0]
            series.index = pd.to_datetime(series.index).tz_localize(None)
            prices_dict[ticker] = series
            
    if len(prices_dict) < 2:
        print("Error: Backtest requires at least 2 cached price files. Please run 'demo.py' first to populate the cache.")
        return
        
    prices_df = pd.DataFrame(prices_dict).dropna()
    prices_df.index = pd.to_datetime(prices_df.index).tz_localize(None)
    prices_df = prices_df.sort_index()
    
    print(f"Loaded {len(prices_df)} days of price history from {prices_df.index.min().date()} to {prices_df.index.max().date()}")
    
    # Calculate daily returns
    returns_df = prices_df.pct_change().dropna()
    
    # 2. Backtest Parameters
    # We will use a 1-year rolling lookback window to calculate portfolio weights
    lookback_days = 252  # ~1 year of trading days
    # Rebalance monthly (approx. every 21 trading days)
    rebalance_freq = 21 
    
    n_days = len(returns_df)
    if n_days <= lookback_days + rebalance_freq:
        print(f"Error: Not enough data points. Need more than {lookback_days + rebalance_freq} days of data.")
        return
        
    # Initialize series to track portfolio values (starting at $1.00)
    opt_portfolio_value = [1.0]
    eq_portfolio_value = [1.0]
    
    # Current active weights
    opt_weights = np.array([1.0 / len(tickers)] * len(tickers))
    eq_weights = np.array([1.0 / len(tickers)] * len(tickers))
    
    # Track rebalance dates and weights history
    rebalance_dates = []
    opt_weights_history = []
    
    print("\nSimulating rolling monthly rebalancing...")
    
    # Loop day by day through the backtesting period
    for i in range(lookback_days, n_days):
        current_date = returns_df.index[i]
        daily_ret = returns_df.iloc[i].values
        
        # Calculate daily change in portfolio values
        opt_daily_return = np.dot(opt_weights, daily_ret)
        eq_daily_return = np.dot(eq_weights, daily_ret)
        
        # Accumulate values
        opt_portfolio_value.append(opt_portfolio_value[-1] * (1.0 + opt_daily_return))
        eq_portfolio_value.append(eq_portfolio_value[-1] * (1.0 + eq_daily_return))
        
        # Update weights based on daily price drift
        opt_weights = opt_weights * (1.0 + daily_ret)
        opt_weights = opt_weights / np.sum(opt_weights) # normalize
        
        eq_weights = eq_weights * (1.0 + daily_ret)
        eq_weights = eq_weights / np.sum(eq_weights) # normalize
        
        # Check if it is rebalancing day (every 21 days)
        if (i - lookback_days) % rebalance_freq == 0:
            rebalance_dates.append(current_date)
            
            # --- Optimize weights using Riskfolio-Lib ---
            # Use the past 1 year of returns as historical lookback data
            historical_returns = returns_df.iloc[i - lookback_days:i]
            
            try:
                # Initialize Portfolio
                port = rp.Portfolio(returns=historical_returns)
                port.assets_stats(method_mu='hist', method_cov='hist')
                port.solvers = ['CLARABEL', 'SCS']
                
                # Optimize for Maximum Sharpe Ratio (Mean-Variance)
                w_opt = port.optimization(model='Classic', rm='MV', obj='Sharpe', rf=0.0, hist=True)
                
                # Apply new weights (account for a small 0.1% transaction cost on rebalancing)
                new_opt_weights = w_opt['weights'].values
                
                # Transaction cost calculation based on turnover
                turnover = np.sum(np.abs(new_opt_weights - opt_weights))
                transaction_fee = turnover * 0.001  # 0.1% execution fee
                
                # Apply transaction fee to portfolio value
                opt_portfolio_value[-1] = opt_portfolio_value[-1] * (1.0 - transaction_fee)
                opt_weights = new_opt_weights
                opt_weights_history.append(opt_weights)
            except Exception as e:
                # If optimization fails, keep current weights and print warning
                print(f"Warning: Optimization failed on {current_date.date()}: {e}. Keeping existing weights.")
                
            # Rebalance the Equal-Weighted (1/N) benchmark
            new_eq_weights = np.array([1.0 / len(tickers)] * len(tickers))
            eq_turnover = np.sum(np.abs(new_eq_weights - eq_weights))
            eq_transaction_fee = eq_turnover * 0.001
            
            eq_portfolio_value[-1] = eq_portfolio_value[-1] * (1.0 - eq_transaction_fee)
            eq_weights = new_eq_weights
            
    # 3. Create Performance Results DataFrame
    backtest_dates = returns_df.index[lookback_days-1:]
    results_df = pd.DataFrame(index=backtest_dates)
    results_df['Optimized Portfolio (Sharpe)'] = opt_portfolio_value
    results_df['Equal Weighted Portfolio (1/N)'] = eq_portfolio_value
    
    # 4. Calculate Summary Performance Metrics
    metrics = {}
    for col in results_df.columns:
        # Annualized Return (assuming 252 trading days per year)
        total_return = results_df[col].iloc[-1] - 1.0
        n_years = len(results_df) / 252.0
        cagr = (results_df[col].iloc[-1]) ** (1.0 / n_years) - 1.0
        
        # Annualized Volatility
        daily_returns = results_df[col].pct_change().dropna()
        ann_vol = daily_returns.std() * np.sqrt(252)
        
        # Sharpe Ratio (assuming risk-free rate = 0%)
        sharpe = cagr / ann_vol if ann_vol > 0 else 0
        
        # Max Drawdown
        cum_max = results_df[col].cummax()
        drawdown = (results_df[col] - cum_max) / cum_max
        max_dd = drawdown.min()
        
        metrics[col] = {
            'Total Return (%)': round(total_return * 100, 2),
            'Annualized Return (%)': round(cagr * 100, 2),
            'Annualized Volatility (%)': round(ann_vol * 100, 2),
            'Sharpe Ratio': round(sharpe, 2),
            'Max Drawdown (%)': round(max_dd * 100, 2)
        }
        
    metrics_df = pd.DataFrame(metrics)
    
    print("\n--- Backtest Summary Performance Metrics ---")
    print("--------------------------------------------")
    print(metrics_df)
    
    # 5. Plot Cumulative Performance
    plt.figure(figsize=(12, 6))
    plt.plot(results_df.index, results_df['Optimized Portfolio (Sharpe)'], label='Optimized Portfolio (Sharpe)', color='blue', linewidth=2)
    plt.plot(results_df.index, results_df['Equal Weighted Portfolio (1/N)'], label='Equal-Weighted Portfolio (1/N)', color='gray', linestyle='--', alpha=0.8)
    
    plt.title('Historical Backtest Performance: Optimized Sharpe vs Equal-Weighted', fontsize=14, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Portfolio Value ($)', fontsize=12)
    plt.legend(fontsize=10)
    plt.grid(linestyle=':', alpha=0.6)
    
    # Highlight rebalancing periods
    plt.gcf().autofmt_xdate()
    
    output_path = os.path.join(script_dir, "backtest_performance.png")
    plt.savefig(output_path, bbox_inches='tight')
    plt.close()
    
    print(f"\nCumulative return chart saved to: {output_path}")
    print("Backtest simulation completed successfully!")

if __name__ == "__main__":
    run_backtest()
