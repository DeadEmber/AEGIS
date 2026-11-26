# Provenance of recorded fixtures

Recorded 2026-11-26 ~11:04-11:06 UTC from the AEGIS development container, unauthenticated
public endpoints. These are REAL responses (some truncated to keep the repo small). They are
used for schema/parsing tests only — never as market evidence or backtest data.

| File | Request |
|---|---|
| gt_new.json | GET https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1&include=base_token,quote_token,dex |
| gt_trending.json | GET .../networks/solana/trending_pools?page=1&duration=1h&include=base_token |
| gt_multi.json | GET .../networks/solana/pools/multi/DDcyemq9bkyqtC93djkwaH6VaMKCjVsoCUVGV29q7teB,CXx1hDucDWLhZED32skxAAtvvEdvLWzajYjU8621Js7L |
| gt_token.json | GET .../networks/solana/tokens/Fo2HAwr7ZAUM2bSZ7MWSi16e3aWGpbw5MsUDYCttpump |
| gt_token_info.json | GET .../networks/solana/tokens/Fo2HAwr7ZAUM2bSZ7MWSi16e3aWGpbw5MsUDYCttpump/info |
| gt_ohlcv.json | GET .../networks/solana/pools/DDcyemq9.../ohlcv/minute?aggregate=5&limit=10&currency=usd |
| gt_trades.json | GET .../networks/solana/pools/DDcyemq9.../trades (truncated to 25 of 300 trades) |
| gt_token_pools.json | GET .../networks/solana/tokens/Fo2HAwr7.../pools?page=1 |
| ds_token_pairs.json | GET https://api.dexscreener.com/token-pairs/v1/solana/Fo2HAwr7... |
| ds_tokens.json | GET https://api.dexscreener.com/tokens/v1/solana/Fo2HAwr7...,9PDLcx5c... |
| ds_profiles.json | GET https://api.dexscreener.com/token-profiles/latest/v1 (truncated to 5) |
| jup_buy.json | GET https://lite-api.jup.ag/swap/v1/quote?inputMint=SOL&outputMint=Fo2HAwr7...&amount=14000000&slippageBps=300 |
| jup_sell.json | GET https://lite-api.jup.ag/swap/v1/quote?inputMint=Fo2HAwr7...&outputMint=SOL&amount=100000000&slippageBps=300 |
| jup_ultra.json | GET https://lite-api.jup.ag/ultra/v1/order?inputMint=SOL&outputMint=Fo2HAwr7...&amount=14000000 |
| jup_token.json | GET https://lite-api.jup.ag/tokens/v2/search?query=Fo2HAwr7... |
| rpc_mint.json | POST https://api.mainnet-beta.solana.com getAccountInfo(Fo2HAwr7..., jsonParsed) — pump.fun Token-2022 mint |
| rpc_mint_t22.json | POST getAccountInfo(2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo = PYUSD) — Token-2022 with permanentDelegate/transferHook/transferFee |
| rpc_largest.json | POST getTokenLargestAccounts — returned HTTP-level JSON-RPC error 429 (kept as an error fixture) |
| rpc_rent165.json | POST getMinimumBalanceForRentExemption(165) |
| rpc_prio.json | POST getRecentPrioritizationFees (truncated to 20) |
| fx.json | GET https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD |
| cg_price.json | GET https://api.coingecko.com/api/v3/simple/price?ids=solana,bitcoin&vs_currencies=eur,usd&include_last_updated_at=true |
| gt_ratelimited.json | (synthetic copy of a real 429 body returned by GeckoTerminal during recording) |
