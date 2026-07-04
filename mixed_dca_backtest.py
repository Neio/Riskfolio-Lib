import os
import datetime
import numpy as np
import pandas as pd
import yfinance as yf
import riskfolio as rp
import dotenv
import matplotlib
# Use a non-interactive backend for saving plots
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Load environment variables for Tiingo fallback
dotenv.load_dotenv()
TIINGO_API_KEY = os.environ.get("TIINGO_API_KEY")

def run_mixed_backtest():
    print("--- Starting Mixed Portfolio (Superstars, Normals, Underperformers) DCA Backtest ---")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_dir = os.path.join(script_dir, "cached_prices")
    os.makedirs(cache_dir, exist_ok=True)
    
    # Define mixed universe:
    # 1 Superstar: NVDA
    # 3 Normals: PG (Procter & Gamble), KO (Coca-Cola), WMT (Walmart)
    # 3 Underperformers: INTC (Intel), PFE (Pfizer), PYPL (PayPal)
    stock_tickers = ['NVDA', 'PG', 'KO', 'WMT', 'INTC', 'PFE', 'PYPL']
    all_tickers = stock_tickers + ['QQQ']
    
    prices_dict = {}
    today = datetime.datetime.now().date()
    end_date = datetime.datetime.now().strftime('%Y-%m-%d')
    default_start_date = (datetime.datetime.now() - datetime.timedelta(days=3*365)).strftime('%Y-%m-%d')
    
    tickers_to_download = []
    start_dates = []
    
    # Check cache status
    for ticker in all_tickers:
        ticker_file = os.path.join(cache_dir, f"{ticker}.csv")
        if os.path.exists(ticker_file):
            df = pd.read_csv(ticker_file, parse_dates=True, index_col=0)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            prices_dict[ticker] = df
            
            last_date = df.index.max().date()
            if last_date < today - datetime.timedelta(days=1):
                next_start = (last_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                tickers_to_download.append(ticker)
                start_dates.append(next_start)
        else:
            tickers_to_download.append(ticker)
            start_dates.append(default_start_date)
            
    # Download missing data
    if tickers_to_download:
        min_start_date = min(start_dates)
        print(f"Downloading data for new tickers: {tickers_to_download} from {min_start_date}...")
        download_success = False
        
        try:
            import requests
            session = requests.Session()
            data = yf.download(tickers_to_download, start=min_start_date, end=end_date, session=session)
            
            if not data.empty and all(t in data.columns.get_level_values(-1) if isinstance(data.columns, pd.MultiIndex) else t in data.columns for t in tickers_to_download):
                for ticker in tickers_to_download:
                    ticker_file = os.path.join(cache_dir, f"{ticker}.csv")
                    if len(tickers_to_download) == 1:
                        series = data['Adj Close'] if 'Adj Close' in data.columns else data['Close']
                    else:
                        series = data['Adj Close'][ticker] if 'Adj Close' in data.columns else data['Close'][ticker]
                    
                    if isinstance(series, pd.DataFrame):
                        series = series.iloc[:, 0]
                        
                    series.name = ticker
                    new_df = series.to_frame()
                    new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
                    
                    if ticker in prices_dict:
                        combined_df = pd.concat([prices_dict[ticker], new_df])
                        combined_df = combined_df[~combined_df.index.duplicated(keep='last')].sort_index()
                    else:
                        combined_df = new_df
                        
                    combined_df.to_csv(ticker_file)
                    prices_dict[ticker] = combined_df
                    print(f"-> Updated {ticker} via yfinance")
                download_success = True
        except Exception as e:
            print(f"yfinance failed: {e}")
            
        if not download_success:
            if TIINGO_API_KEY:
                print("Attempting fallback to Tiingo API...")
                import requests
                for ticker, t_start in zip(tickers_to_download, start_dates):
                    ticker_file = os.path.join(cache_dir, f"{ticker}.csv")
                    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate={t_start}&endDate={end_date}&token={TIINGO_API_KEY}"
                    try:
                        response = requests.get(url, headers={'Content-Type': 'application/json'})
                        if response.status_code == 200:
                            json_data = response.json()
                            if json_data:
                                df_tiingo = pd.DataFrame(json_data)
                                df_tiingo['date'] = pd.to_datetime(df_tiingo['date']).dt.tz_localize(None)
                                df_tiingo.set_index('date', inplace=True)
                                
                                col = 'adjClose' if 'adjClose' in df_tiingo.columns else 'close'
                                series = df_tiingo[col]
                                series.name = ticker
                                
                                new_df = series.to_frame()
                                new_df.index = pd.to_datetime(new_df.index).tz_localize(None)
                                
                                if ticker in prices_dict:
                                    combined_df = pd.concat([prices_dict[ticker], new_df])
                                    combined_df = combined_df[~combined_df.index.duplicated(keep='last')].sort_index()
                                else:
                                    combined_df = new_df
                                    
                                combined_df.to_csv(ticker_file)
                                prices_dict[ticker] = combined_df
                                print(f"-> Updated {ticker} via Tiingo")
                            else:
                                print(f"No data from Tiingo for {ticker}")
                        else:
                            print(f"Tiingo failed for {ticker} (status: {response.status_code})")
                    except Exception as ex:
                        print(f"Tiingo error for {ticker}: {ex}")
            else:
                print("Warning: Network failed and no TIINGO_API_KEY available.")
                
    # Consolidate prices
    final_prices = {}
    for ticker in all_tickers:
        if ticker in prices_dict:
            series = prices_dict[ticker].iloc[:, 0]
            series.index = pd.to_datetime(series.index).tz_localize(None)
            final_prices[ticker] = series
            
    prices_df = pd.DataFrame(final_prices).dropna()
    prices_df = prices_df.sort_index()
    
    print(f"\nAligned stock dataset from {prices_df.index.min().date()} to {prices_df.index.max().date()}")
    
    returns_df = prices_df[stock_tickers].pct_change().dropna()
    qqq_returns = prices_df['QQQ'].pct_change().dropna()
    n_days = len(returns_df)
    
    # Backtest setup
    start_idx = 504
    rebalance_freq = 21
    dca_amount = 2000.0
    
    opt_value = []
    eq_value = []
    qqq_value = []
    cash_value = []
    
    opt_holdings = pd.Series(0.0, index=stock_tickers)
    eq_holdings = pd.Series(0.0, index=stock_tickers)
    
    qqq_holdings = 0.0
    cash_holdings = 0.0
    
    # To track what the average weight allocation was for each asset during optimization
    weight_tracker = pd.DataFrame(0.0, index=returns_df.index[start_idx:], columns=stock_tickers)
    
    print(f"\nSimulating DCA of ${dca_amount:,.2f} monthly...")
    
    for i in range(start_idx, n_days):
        current_date = returns_df.index[i]
        
        # Prices change
        daily_ret = returns_df.iloc[i]
        qqq_daily_ret = qqq_returns.iloc[i]
        
        opt_holdings = opt_holdings * (1.0 + daily_ret)
        eq_holdings = eq_holdings * (1.0 + daily_ret)
        qqq_holdings = qqq_holdings * (1.0 + qqq_daily_ret)
        
        # Rebalance day
        if (i - start_idx) % rebalance_freq == 0:
            cash_holdings += dca_amount
            qqq_holdings += dca_amount
            
            opt_total_before = opt_holdings.sum() + dca_amount
            eq_total_before = eq_holdings.sum() + dca_amount
            
            # Riskfolio Sharpe
            lookback_data = returns_df.iloc[i - 252:i]
            try:
                port = rp.Portfolio(returns=lookback_data)
                port.assets_stats(method_mu='hist', method_cov='hist')
                port.solvers = ['CLARABEL', 'SCS']
                w_opt = port.optimization(model='Classic', rm='MV', obj='Sharpe', rf=0.0, hist=True)
                opt_target_weights = w_opt['weights']
                
                # Transaction fees
                opt_target_dollars = opt_target_weights * opt_total_before
                opt_turnover = np.sum(np.abs(opt_target_dollars - opt_holdings))
                opt_fee = opt_turnover * 0.001
                
                opt_holdings = opt_target_weights * (opt_total_before - opt_fee)
            except Exception as e:
                # Fallback to equal weighting if fail
                opt_target_weights = pd.Series(1.0 / len(stock_tickers), index=stock_tickers)
                opt_holdings += opt_target_weights * dca_amount
                
            # Equal Weighted rebalance
            eq_target_weights = pd.Series(1.0 / len(stock_tickers), index=stock_tickers)
            eq_target_dollars = eq_target_weights * eq_total_before
            eq_turnover = np.sum(np.abs(eq_target_dollars - eq_holdings))
            eq_fee = eq_turnover * 0.001
            eq_holdings = eq_target_weights * (eq_total_before - eq_fee)
            
        # Record weights
        current_w = opt_holdings / (opt_holdings.sum() if opt_holdings.sum() > 0 else 1.0)
        weight_tracker.loc[current_date] = current_w.values
        
        opt_value.append(opt_holdings.sum())
        eq_value.append(eq_holdings.sum())
        qqq_value.append(qqq_holdings)
        cash_value.append(cash_holdings)
        
    # Results DataFrame
    backtest_dates = returns_df.index[start_idx:]
    results_df = pd.DataFrame(index=backtest_dates)
    results_df['Optimized Portfolio (Sharpe)'] = opt_value
    results_df['Equal-Weighted Portfolio (1/N)'] = eq_value
    results_df['QQQ Benchmark (ETF)'] = qqq_value
    results_df['Cash Savings (Principal)'] = cash_value
    
    # 4. Summary Performance Metrics
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
    
    print("\n--- Performance Metrics (Mixed Universe) ---")
    print("--------------------------------------------")
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)
    print(metrics_df)
    
    # Print Average Allocation of the Optimized Portfolio to see how it treated garbage stocks
    avg_weights = (weight_tracker.mean() * 100).round(2)
    print("\n--- Average Weight Allocation by Asset in Optimized Portfolio ---")
    print("-----------------------------------------------------------------")
    print(avg_weights.sort_values(ascending=False))
    
    # 5. Plot Performance (Normalized as % ROI)
    plt.figure(figsize=(12, 6))
    
    # Calculate % Return on Invested Capital (ROI)
    roi_opt = (results_df['Optimized Portfolio (Sharpe)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    roi_eq = (results_df['Equal-Weighted Portfolio (1/N)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    roi_qqq = (results_df['QQQ Benchmark (ETF)'] - results_df['Cash Savings (Principal)']) / results_df['Cash Savings (Principal)'] * 100
    
    plt.plot(results_df.index, roi_opt, label='Optimized Portfolio (Sharpe)', color='blue', linewidth=2.5)
    plt.plot(results_df.index, roi_eq, label='Equal-Weighted Portfolio (1/N)', color='orange', linewidth=2)
    plt.plot(results_df.index, roi_qqq, label='QQQ Benchmark (ETF)', color='green', linewidth=1.5, linestyle='-.')
    plt.axhline(0, label='Cash Savings (0% Return)', color='gray', linewidth=1.5, linestyle='--')
    
    plt.title('Monthly DCA Simulation ($2,000/Month): Mixed Universe % ROI Comparison', fontsize=14, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Return on Invested Capital (%)', fontsize=12)
    plt.legend(fontsize=10)
    plt.grid(linestyle=':', alpha=0.6)
    
    plt.gcf().autofmt_xdate()
    
    output_path = os.path.join(script_dir, "mixed_dca_performance.png")
    plt.savefig(output_path, bbox_inches='tight')
    plt.close()
    
    print(f"\nMixed DCA performance chart saved to: {output_path}")

if __name__ == "__main__":
    run_mixed_backtest()
