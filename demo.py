import os
import datetime
import pandas as pd
import yfinance as yf
import riskfolio as rp
import matplotlib.pyplot as plt

# 1. Define the tickers and configuration
tickers = ['TSLA', 'NVDA', 'PANW', 'MU', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']

# Set this to True to query Yahoo Finance for the latest stock prices.
# Set to False to run completely offline/strictly using cached data.
ONLINE_MODE = True

script_dir = os.path.dirname(os.path.abspath(__file__))
cache_dir = os.path.join(script_dir, "cached_prices")
os.makedirs(cache_dir, exist_ok=True)

# 2. Check and load cached price files
prices_dict = {}
tickers_to_download = []
start_dates = []

today = datetime.datetime.now().date()
end_date = datetime.datetime.now().strftime('%Y-%m-%d')
default_start_date = (datetime.datetime.now() - datetime.timedelta(days=3*365)).strftime('%Y-%m-%d')

fallback_source = os.path.join(script_dir, "tests", "stock_prices.csv")
fallback_df = None
if os.path.exists(fallback_source):
    fallback_df = pd.read_csv(fallback_source, parse_dates=True, index_col=0)

for ticker in tickers:
    ticker_file = os.path.join(cache_dir, f"{ticker}.csv")
    
    if os.path.exists(ticker_file):
        df = pd.read_csv(ticker_file, parse_dates=True, index_col=0)
        df.index = pd.to_datetime(df.index)
        prices_dict[ticker] = df
        
        last_date = df.index.max().date()
        if ONLINE_MODE and last_date < today - datetime.timedelta(days=1):
            next_start = (last_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            tickers_to_download.append(ticker)
            start_dates.append(next_start)
    else:
        if fallback_df is not None and ticker in fallback_df.columns:
            print(f"Cache for {ticker} not found. Extracting from local fallback file: {fallback_source}")
            series = fallback_df[ticker]
            df = series.to_frame()
            df.index = pd.to_datetime(df.index)
            df.to_csv(ticker_file)
            prices_dict[ticker] = df
            
            last_date = df.index.max().date()
            if ONLINE_MODE and last_date < today - datetime.timedelta(days=1):
                next_start = (last_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                tickers_to_download.append(ticker)
                start_dates.append(next_start)
        else:
            # If the cache is entirely missing and no local fallback exists, we must download
            tickers_to_download.append(ticker)
            start_dates.append(default_start_date)

# 3. Download updates in bulk if required
if tickers_to_download:
    min_start_date = min(start_dates)
    print(f"\nRequesting updates in a single bulk API call starting from {min_start_date}...")
    try:
        import requests
        session = requests.Session()
        data = yf.download(tickers_to_download, start=min_start_date, end=end_date, session=session)
        
        if not data.empty:
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
                new_df.index = pd.to_datetime(new_df.index)
                
                if ticker in prices_dict:
                    combined_df = pd.concat([prices_dict[ticker], new_df])
                    combined_df = combined_df[~combined_df.index.duplicated(keep='last')].sort_index()
                else:
                    combined_df = new_df
                    
                combined_df.to_csv(ticker_file)
                prices_dict[ticker] = combined_df
                print(f"-> Updated cache file for {ticker}")
        else:
            print("Bulk download returned empty data. Using cached data.")
    except Exception as e:
        print(f"\nWarning: Bulk update failed: {e}. Falling back to cached data.")

# 4. Consolidate DataFrames
final_prices = {ticker: prices_dict[ticker].iloc[:, 0] for ticker in tickers if ticker in prices_dict}
prices_df = pd.DataFrame(final_prices).dropna()
print(f"\nAligned stock prices for tickers: {list(prices_df.columns)}")

if len(prices_df.columns) < 2:
    print("\nError: At least 2 assets are required to run portfolio optimization and hierarchical clustering.")
    print("Since the Yahoo Finance bulk download failed (due to API rate-limiting) and there is no cached data")
    print("available for the other new assets, we cannot proceed.")
    print("\nPlease wait a few minutes for the rate limit to expire, then run the script again to fetch and cache the data.")
    exit(1)

# Calculate returns
returns = prices_df.pct_change().dropna()

# 5. Run Classic Mean-Variance Optimization
print("\n--- Running Classic Mean-Variance Portfolio Optimization ---")
port = rp.Portfolio(returns=returns)
port.assets_stats(method_mu='hist', method_cov='hist')
port.solvers = ['CLARABEL', 'SCS']
weights = port.optimization(model='Classic', rm='MV', obj='Sharpe', rf=0.0, hist=True)

print("\nOptimal Sharpe Allocation Weights:")
print(weights.round(4) * 100)

# Save Pie Chart
ax = rp.plot_pie(w=weights, title='Sharpe Portfolio Allocation', cmap="tab20", height=6, width=8, ax=None)
pie_output = os.path.join(script_dir, "portfolio_pie.png")
ax.figure.savefig(pie_output, bbox_inches='tight')
plt.close(ax.figure)
print(f"Pie chart saved to: {pie_output}")

# 6. Run Hierarchical Clustering (Dendrogram)
print("\n--- Running Hierarchical Clustering Analysis ---")
hc_port = rp.HCPortfolio(returns=returns)
hc_port.optimization(model='HRP', codependence='pearson', rm='vol', rf=0.0, linkage='single')

ax_dendro = rp.plot_dendrogram(returns, codependence='pearson', linkage='single', k=None, max_k=10, leaf_order=True, ax=None)
dendro_output = os.path.join(script_dir, "portfolio_dendrogram.png")
ax_dendro.figure.savefig(dendro_output, bbox_inches='tight')
plt.close(ax_dendro.figure)
print(f"Dendrogram saved to: {dendro_output}")

# 7. Convert Weights to Actual Share Allocations
print("\n--- Converting Portfolio Weights to Share Allocations ---")
budget = 100000.0  # Change this to your desired investing budget (in USD)
print(f"Investing Budget: ${budget:,.2f}")

# Get the latest price of each stock from the dataset
latest_prices = prices_df.iloc[-1]

allocation_df = pd.DataFrame(index=weights.index)
allocation_df['Weight (%)'] = (weights['weights'] * 100).round(2)
allocation_df['Latest Price ($)'] = latest_prices.round(2)
allocation_df['Allocation ($)'] = (weights['weights'] * budget).round(2)
allocation_df['Shares to Buy'] = (allocation_df['Allocation ($)'] / allocation_df['Latest Price ($)']).round(2)

print("\nAsset Purchase Breakdown:")
print("-------------------------")
print(allocation_df)

print("\nAll tasks completed successfully!")
