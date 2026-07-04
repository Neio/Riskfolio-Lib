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
    print("--- Starting Riskfolio-Lib Lookback Window Parameter Comparison ---")
    
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
    prices_df = prices_df.sort_index()
    
    print(f"Loaded {len(prices_df)} days of price history from {prices_df.index.min().date()} to {prices_df.index.max().date()}")
    
    # Calculate daily returns
    returns_df = prices_df.pct_change().dropna()
    n_days = len(returns_df)
    
    # Define parameters to test
    # We use 504 days (2 years) as the start index so that the 2-year lookback has enough initial data.
    # All strategies are backtested on the exact same dates from day 504 to the end of the dataset.
    start_idx = 504
    rebalance_freq = 21 # monthly
    
    if n_days <= start_idx + rebalance_freq:
        print(f"Error: Not enough data points. Need more than {start_idx + rebalance_freq} days of data.")
        return
        
    # We will test lookback windows: 6 Months, 1 Year, 2 Years
    lookback_configs = {
        'Optimized (6 Months)': 126,
        'Optimized (1 Year)': 252,
        'Optimized (2 Years)': 504
    }
    
    results = {}
    
    # Run the backtest for each parameter configuration
    for name, lookback_days in lookback_configs.items():
        print(f"\nRunning backtest for: {name} (Lookback: {lookback_days} trading days)...")
        
        portfolio_value = [1.0]
        weights = np.array([1.0 / len(tickers)] * len(tickers))
        
        for i in range(start_idx, n_days):
            current_date = returns_df.index[i]
            daily_ret = returns_df.iloc[i].values
            
            # Track daily return
            daily_port_return = np.dot(weights, daily_ret)
            portfolio_value.append(portfolio_value[-1] * (1.0 + daily_port_return))
            
            # Price drift update
            weights = weights * (1.0 + daily_ret)
            weights = weights / np.sum(weights)
            
            # Rebalancing day
            if (i - start_idx) % rebalance_freq == 0:
                historical_returns = returns_df.iloc[i - lookback_days:i]
                
                try:
                    port = rp.Portfolio(returns=historical_returns)
                    port.assets_stats(method_mu='hist', method_cov='hist')
                    port.solvers = ['CLARABEL', 'SCS']
                    
                    w_opt = port.optimization(model='Classic', rm='MV', obj='Sharpe', rf=0.04/252, hist=True)
                    new_weights = w_opt['weights'].values
                    
                    # Deduct transaction fee
                    turnover = np.sum(np.abs(new_weights - weights))
                    transaction_fee = turnover * 0.001
                    portfolio_value[-1] = portfolio_value[-1] * (1.0 - transaction_fee)
                    
                    weights = new_weights
                except Exception as e:
                    pass # Keep drifted weights if optimization fails
                    
        results[name] = portfolio_value

    # Run Benchmark: Equal-Weighted (1/N) rebalanced monthly
    print("\nRunning benchmark: Equal-Weighted (1/N)...")
    eq_portfolio_value = [1.0]
    eq_weights = np.array([1.0 / len(tickers)] * len(tickers))
    
    for i in range(start_idx, n_days):
        daily_ret = returns_df.iloc[i].values
        eq_daily_return = np.dot(eq_weights, daily_ret)
        eq_portfolio_value.append(eq_portfolio_value[-1] * (1.0 + eq_daily_return))
        
        # Price drift update
        eq_weights = eq_weights * (1.0 + daily_ret)
        eq_weights = eq_weights / np.sum(eq_weights)
        
        # Rebalance day
        if (i - start_idx) % rebalance_freq == 0:
            new_eq_weights = np.array([1.0 / len(tickers)] * len(tickers))
            eq_turnover = np.sum(np.abs(new_eq_weights - eq_weights))
            eq_transaction_fee = eq_turnover * 0.001
            eq_portfolio_value[-1] = eq_portfolio_value[-1] * (1.0 - eq_transaction_fee)
            eq_weights = new_eq_weights
            
    results['Equal-Weighted (1/N)'] = eq_portfolio_value
    
    # 3. Create Performance Results DataFrame
    backtest_dates = returns_df.index[start_idx-1:]
    results_df = pd.DataFrame(results, index=backtest_dates)
    
    # 4. Calculate Summary Performance Metrics
    metrics = {}
    for col in results_df.columns:
        total_return = results_df[col].iloc[-1] - 1.0
        n_years = len(results_df) / 252.0
        cagr = (results_df[col].iloc[-1]) ** (1.0 / n_years) - 1.0
        
        daily_returns = results_df[col].pct_change().dropna()
        ann_vol = daily_returns.std() * np.sqrt(252)
        sharpe = cagr / ann_vol if ann_vol > 0 else 0
        
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
    
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)
    
    print("\n--- Lookback Parameter Backtest Summary ---")
    print("--------------------------------------------")
    print(metrics_df)
    
    # 5. Plot Cumulative Performance
    plt.figure(figsize=(12, 6))
    colors_map = {
        'Optimized (6 Months)': 'orange',
        'Optimized (1 Year)': 'blue',
        'Optimized (2 Years)': 'green',
        'Equal-Weighted (1/N)': 'gray'
    }
    
    for col in results_df.columns:
        color = colors_map.get(col, 'black')
        linewidth = 2.5 if col != 'Equal-Weighted (1/N)' else 1.5
        linestyle = '-' if col != 'Equal-Weighted (1/N)' else '--'
        plt.plot(results_df.index, results_df[col], label=col, color=color, linewidth=linewidth, linestyle=linestyle)
        
    plt.title('Rolling Backtest Comparison: Parameter Sensitivity Analysis', fontsize=14, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Portfolio Value ($)', fontsize=12)
    plt.legend(fontsize=10)
    plt.grid(linestyle=':', alpha=0.6)
    
    plt.gcf().autofmt_xdate()
    
    output_path = os.path.join(script_dir, "backtest_performance.png")
    plt.savefig(output_path, bbox_inches='tight')
    plt.close()
    
    print(f"\nCumulative return comparison chart saved to: {output_path}")
    print("Parameter sensitivity simulation completed successfully!")

if __name__ == "__main__":
    run_backtest()
