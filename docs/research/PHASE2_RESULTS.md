# AEGIS — Fase 2: resultados empíricos reproducibles

Generado por `aegis research phase2`. Datos: revisiones fijadas en `aegis.history.registry`. Ninguna cifra es una promesa de rentabilidad: son resultados históricos con los supuestos indicados.

## E4 — Base empírica: cobertura, latencia, estrategias de referencia y replay del motor

Versión 0.2.0, generado 2026-11-26T18:29:12+00:00, 406.1 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share | dataset | owner_reported_eur |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| basic+trailing | 47.84 | 200 | 862 | 576 | -0.1611 | 0.5139 | 136 | 53 | 0.6503 | 0.3348 | pumpfun_corpus | 122.8 |
| strict+hold | 18.7 | 200 | 200 | 0 | -0.6129 | — | 0 | 0 | 0.0032 | 0.2358 | pumpfun_corpus | — |
| strict+trailing | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 | pumpfun_corpus | 198.9 |
| strict+trailing+context_raw | 166.6 | 200 | 318 | 219 | -0.0794 | 0.5205 | 59 | 22 | 0.6763 | 0.8115 | pumpfun_corpus | 210.7 |
| strict+trailing+context_rate | 140 | 200 | 458 | 315 | -0.1129 | 0.5302 | 80 | 31 | 0.668 | 0.6727 | pumpfun_corpus | — |
| strict+aegis_exit | 65.33 | 200 | 464 | 580 | -0.3115 | 0.0152 | 0 | 0 | 0.4449 | 0.5333 | pumpfun_corpus | — |
| strict+trailing (AEGIS 0.2 allocation) | 169.4 | 200 | 299 | 209 | -0.0844 | 0.5598 | 52 | 19 | 0.6716 | 0.7602 | pumpfun_corpus | — |
| basic+trailing | 77.89 | 100 | 65 | 23 | -0.3925 | 0.3478 | 3 | 0 | 0.3806 | 0.6971 | launchtape | — |
| strict+hold | 89.92 | 100 | 21 | 0 | -0.5899 | — | 0 | 0 | 0 | 0.8648 | launchtape | — |
| strict+trailing | 91.37 | 100 | 21 | 6 | -0.4789 | 0 | 0 | 0 | 0.2451 | 0.8986 | launchtape | — |
| strict+trailing+context_raw | 99.93 | 100 | 3 | 1 | -0.1211 | 0 | 0 | 0 | 0.7869 | 0.9915 | launchtape | — |
| strict+trailing+context_rate | 91.37 | 100 | 21 | 6 | -0.4789 | 0 | 0 | 0 | 0.2451 | 0.8986 | launchtape | — |
| strict+aegis_exit | 91.96 | 100 | 21 | 9 | -0.4438 | 0 | 0 | 0 | 0.288 | 0.9067 | launchtape | — |
| strict+trailing (AEGIS 0.2 allocation) | 95.57 | 100 | 6 | 2 | -0.8365 | 0 | 0 | 0 | 0 | 0.9653 | launchtape | — |

### latency

```json
{
 "dataset": "launchtape",
 "observations": 20950,
 "lags": {
  "1": {
   "n": 20930,
   "signed_mean_bps": -11.2,
   "signed_p10_bps": -15.61842883926058,
   "signed_p50_bps": 0.0,
   "signed_p90_bps": 36.14648917062179,
   "abs_p50_bps": 0.0,
   "abs_p90_bps": 136.57567185531653,
   "share_unchanged": 0.554
  },
  "2": {
   "n": 20917,
   "signed_mean_bps": -27.8,
   "signed_p10_bps": -50.981570879976914,
   "signed_p50_bps": 0.0,
   "signed_p90_bps": 72.78164108564096,
   "abs_p50_bps": 0.09153152378185077,
   "abs_p90_bps": 255.77254424624826,
   "share_unchanged": 0.455
  },
  "5": {
   "n": 20882,
   "signed_mean_bps": -70.1,
   "signed_p10_bps": -186.9407663761616,
   "signed_p50_bps": 0.0,
   "signed_p90_bps": 155.48966407319762,
   "abs_p50_bps": 10.524062913873633,
   "abs_p90_bps": 490.58485115737545,
   "share_unchanged": 0.363
  },
  "10": {
   "n": 20814,
   "signed_mean_bps": -140.2,
   "signed_p10_bps": -359.76385467481276,
   "signed_p50_bps": 0.0,
   "signed_p90_bps": 280.2132342020952,
   "abs_p50_bps": 39.11929819918835,
   "abs_p90_bps": 776.2682001980182,
   "share_unchanged": 0.312
  },
  "30": {
   "n": 20542,
   "signed_mean_bps": -424.7,
   "signed_p10_bps": -895.6810259654886,
   "signed_p50_bps": 0.0,
   "signed_p90_bps": 662.8109028058478,
   "abs_p50_bps": 128.38668272029363,
   "abs_p90_bps": 1629.674277966377,
   "share_unchanged": 0.257
  },
  "60": {
   "n": 20134,
   "signed_mean_bps": -865.6,
   "signed_p10_bps": -1679.3798728359066,
   "signed_p50_bps": 0.0,
   "signed_p90_bps": 1043.9053424459748,
   "abs_p50_bps": 256.3066226496402,
   "abs_p90_bps": 2629.7057366571144,
   "share_unchanged": 0.223
  }
 }
}
```

### cross_check

```json
{
 "60": {
  "n": 63,
  "median_ratio": 1.0
 },
 "300": {
  "n": 63,
  "median_ratio": 1.0
 },
 "600": {
  "n": 63,
  "median_ratio": 1.0
 }
}
```

### engine_replay

```json
{
 "default": {
  "cycles": 24,
  "always_reconciled": true,
  "buys": 0,
  "sells": 0,
  "security_assessments": 0,
  "net_worth_liquidable_eur": 100.0
 },
 "no_24h_age_floor": {
  "cycles": 108,
  "always_reconciled": true,
  "buys": 6,
  "sells": 2,
  "security_assessments": 51,
  "net_worth_liquidable_eur": 97.747525031133
 }
}
```

### datasets

```json
{
 "pumpfun_corpus": {
  "revision": "13bec2bf3089d56d50c35e602616d1b993bb37f5",
  "period_observations": [
   "2026-06-19T20:52:40.385069+00:00",
   "2026-07-14T15:27:46.443891+00:00"
  ],
  "tokens": 5700,
  "tokens_with_tradable_observations": 5063,
  "tradable_observations": 1001581,
  "non_tradable_observations": 390303,
  "outcomes_retrospective": 5668,
  "resolution_profile_s": [
   [
    7200.0,
    62.6
   ],
   [
    21600.0,
    312.5
   ],
   [
    86400.0,
    916.0
   ],
   [
    null,
    1818.9
   ]
  ],
  "tracking_hours": {
   "p10": 16.3302343597677,
   "p50": 47.52323707785871,
   "p90": 47.88076272699568
  },
  "first_observation_since_graduation_s": {
   "p10": 19.86,
   "p50": 69.38,
   "p90": 87845.81
  },
  "gaps": [
   {
    "from": "2026-07-03T00:00:00+00:00",
    "to": "2026-07-04T00:00:00+00:00",
    "why": "no graduations recorded (collection outage or gap; KNOWN_ISSUES \u00a75.5 for Jul 3)"
   },
   {
    "from": "2026-07-12T00:00:00+00:00",
    "to": "2026-07-13T00:00:00+00:00",
    "why": "no graduations recorded (collection outage or gap; KNOWN_ISSUES \u00a75.5 for Jul 3)"
   }
  ],
  "quality": {
   "pool_sentinel": 1448,
   "incomplete_data_rows": 48,
   "non_tradable_rows": 390303,
   "concentration_suspect_tokens": 0,
   "missing_reserves_rows": 0
  }
 },
 "launchtape": {
  "revision": "791aeee832cb9a10f55361f10972b33272c857f8",
  "period_observations": [
   "2026-10-10T14:00:15.002000+00:00",
   "2026-10-10T15:59:57.199000+00:00"
  ],
  "tokens": 119,
  "tokens_with_tradable_observations": 82,
  "tradable_observations": 7579,
  "non_tradable_observations": 0,
  "outcomes_retrospective": 67,
  "resolution_profile_s": [
   [
    null,
    15.0
   ]
  ],
  "tracking_hours": {
   "p10": 0.1875,
   "p50": 0.5005658333831363,
   "p90": 0.5011838888459735
  },
  "first_observation_since_graduation_s": {
   "p10": 15.0,
   "p50": 15.0,
   "p90": 15.0
  },
  "gaps": [
   {
    "from": "2026-10-10T14:00:22.088000+00:00",
    "to": "2026-10-10T14:00:23.203000+00:00",
    "why": "launchtape feed down/up"
   },
   {
    "from": "2026-10-10T14:00:30.119000+00:00",
    "to": "2026-10-10T14:00:31.502000+00:00",
    "why": "launchtape feed down/up"
   }
  ],
  "quality": {
   "trades": 417347,
   "virtual_quote_carried": 385103,
   "late_grad": 0
  }
 }
}
```

## E5 — Liquidez: reinversión none/limited/full/contextual × pacing (variable única)

Versión 0.2.0, generado 2026-11-26T18:33:04+00:00, 232.5 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| paced+none | 190.6 | 200 | 148 | 102 | -0.04 | 0.549 | 25 | 12 | 0.6672 | 0.8388 |
| paced+limited | 169.4 | 200 | 299 | 209 | -0.0844 | 0.5598 | 52 | 19 | 0.6716 | 0.7602 |
| paced+full | 151.6 | 200 | 413 | 282 | -0.0939 | 0.5319 | 76 | 29 | 0.6619 | 0.6971 |
| paced+contextual | 151.2 | 200 | 410 | 279 | -0.0957 | 0.5376 | 76 | 29 | 0.6627 | 0.6973 |
| unpaced+none | 170.1 | 200 | 200 | 141 | -0.1488 | 0.5887 | 33 | 12 | 0.6746 | 0.8063 |
| unpaced+limited | 160.9 | 200 | 403 | 284 | -0.0799 | 0.5317 | 72 | 28 | 0.6758 | 0.7203 |
| unpaced+full | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 |
| unpaced+contextual | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 |

## E6 — Selección: ablación de factores y normalización del contexto por cobertura

Versión 0.2.0, generado 2026-11-26T18:48:52+00:00, 947.1 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| strict (reference) | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 |
| basic (reference) | 47.84 | 200 | 862 | 576 | -0.1611 | 0.5139 | 136 | 53 | 0.6503 | 0.3348 |
| strict-liquidity>=20k->10k | 55.04 | 200 | 907 | 591 | -0.1354 | 0.5059 | 136 | 68 | 0.6654 | 0.3824 |
| strict-no_mcap_range | 143.3 | 200 | 467 | 322 | -0.1009 | 0.528 | 81 | 32 | 0.6676 | 0.6733 |
| strict-no_volume_m5 | 142.6 | 200 | 469 | 323 | -0.1035 | 0.5232 | 81 | 32 | 0.6685 | 0.6769 |
| strict-no_buy_sell_ratio | 94.13 | 200 | 675 | 463 | -0.149 | 0.5097 | 107 | 39 | 0.6709 | 0.5486 |
| strict-no_price_change_m5 | 140.4 | 200 | 484 | 334 | -0.1036 | 0.5269 | 83 | 34 | 0.6645 | 0.6658 |
| strict-window_to_6h | 137 | 200 | 558 | 388 | -0.0886 | 0.5052 | 94 | 38 | 0.6761 | 0.6377 |
| basic+liquidity20k | 92.83 | 200 | 680 | 467 | -0.1506 | 0.5032 | 107 | 39 | 0.6691 | 0.5474 |
| basic+volume_m5 | 47.88 | 200 | 857 | 570 | -0.1611 | 0.5193 | 136 | 54 | 0.6501 | 0.335 |
| basic+buy_sell_ratio | 51.34 | 200 | 847 | 544 | -0.1452 | 0.5018 | 124 | 60 | 0.6519 | 0.361 |
| basic+price_change_m5 | 47.37 | 200 | 849 | 565 | -0.1639 | 0.508 | 129 | 54 | 0.6509 | 0.3405 |
| context_raw>=10 | 152.3 | 200 | 423 | 295 | -0.0908 | 0.5119 | 76 | 29 | 0.6716 | 0.7102 |
| context_raw>=20 | 166.6 | 200 | 318 | 219 | -0.0794 | 0.5205 | 59 | 22 | 0.6763 | 0.8115 |
| context_raw>=40 | 195.2 | 200 | 72 | 51 | -0.0459 | 0.4706 | 12 | 4 | 0.6743 | 0.9627 |
| context_rate>=0.01 | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 |
| context_rate>=0.02 | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 |
| context_rate>=0.05 | 140 | 200 | 458 | 315 | -0.1129 | 0.5302 | 80 | 31 | 0.668 | 0.6727 |
| context_rate>=0.10 | 154 | 200 | 238 | 163 | -0.1881 | 0.5215 | 37 | 10 | 0.6529 | 0.7352 |

### coverage_vs_context

```json
{
 "days": [
  "2026-06-19",
  "2026-06-20",
  "2026-06-21",
  "2026-06-22",
  "2026-06-23",
  "2026-06-24",
  "2026-06-25",
  "2026-06-26",
  "2026-06-27",
  "2026-06-28",
  "2026-06-29",
  "2026-06-30",
  "2026-07-01",
  "2026-07-02",
  "2026-07-04",
  "2026-07-05",
  "2026-07-06",
  "2026-07-07",
  "2026-07-08",
  "2026-07-09",
  "2026-07-10",
  "2026-07-11",
  "2026-07-13",
  "2026-07-14"
 ],
 "graduations": [
  15,
  56,
  15,
  94,
  124,
  29,
  18,
  85,
  69,
  142,
  117,
  408,
  98,
  41,
  91,
  456,
  667,
  710,
  487,
  498,
  461,
  380,
  322,
  317
 ],
 "strict_opportunities": [
  0,
  5,
  5,
  21,
  20,
  15,
  8,
  26,
  18,
  12,
  25,
  24,
  16,
  6,
  8,
  27,
  41,
  40,
  24,
  29,
  25,
  24,
  16,
  18
 ],
 "opportunity_rate": [
  0.0,
  0.0893,
  0.3333,
  0.2234,
  0.1613,
  0.5172,
  0.4444,
  0.3059,
  0.2609,
  0.0845,
  0.2137,
  0.0588,
  0.1633,
  0.1463,
  0.0879,
  0.0592,
  0.0615,
  0.0563,
  0.0493,
  0.0582,
  0.0542,
  0.0632,
  0.0497,
  0.0568
 ],
 "corr_graduations_vs_raw_count": 0.827,
 "corr_graduations_vs_rate": -0.5861
}
```

## E7 — Selección supervisada walk-forward: diagnóstico estadístico y evaluación económica

Versión 0.2.0, generado 2026-11-26T18:36:04+00:00, 818.2 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| champion strict+trailing | 155 | 200 | 389 | 267 | -0.0921 | 0.5206 | 69 | 27 | 0.6685 | 0.7282 |
| basic+trailing | 38.63 | 200 | 683 | 430 | -0.227 | 0.5116 | 97 | 35 | 0.6346 | 0.2911 |
| heuristic EPI/MRI on basic (D) | 200 | 200 | 0 | 0 | — | — | 0 | 0 | — | 1 |
| model(basic) p>=0.4 | 32.68 | 200 | 620 | 396 | -0.2712 | 0.4722 | 75 | 21 | 0.6295 | 0.382 |
| model(basic) p>=0.5 | 46.42 | 200 | 646 | 415 | -0.2321 | 0.4843 | 81 | 28 | 0.6507 | 0.4356 |
| model(basic) p>=0.6 | 94.34 | 200 | 464 | 293 | -0.2155 | 0.4505 | 64 | 20 | 0.6496 | 0.5601 |
| model(strict) p>=0.5 | 186.2 | 200 | 159 | 122 | -0.0719 | 0.5 | 23 | 8 | 0.7172 | 0.8846 |

### statistical_out_of_sample

```json
[
 {
  "fold": "wf0",
  "train_n": 134,
  "tau": 0.5,
  "n": 164,
  "base_rate": 0.3537,
  "auc": 0.5529,
  "brier": 0.2563,
  "log_loss": 0.7217,
  "ece": 0.1655,
  "reliability": [
   {
    "bin": 0,
    "n": 33,
    "mean_pred": 0.1578,
    "observed": 0.303
   },
   {
    "bin": 1,
    "n": 33,
    "mean_pred": 0.334,
    "observed": 0.3939
   },
   {
    "bin": 2,
    "n": 32,
    "mean_pred": 0.469,
    "observed": 0.25
   },
   {
    "bin": 3,
    "n": 33,
    "mean_pred": 0.5495,
    "observed": 0.3636
   },
   {
    "bin": 4,
    "n": 33,
    "mean_pred": 0.6739,
    "observed": 0.4545
   }
  ],
  "mean_value_selected": 0.8181,
  "mean_value_all": 0.881
 },
 {
  "fold": "wf1",
  "train_n": 250,
  "tau": 0.4,
  "n": 123,
  "base_rate": 0.3252,
  "auc": 0.4843,
  "brier": 0.2494,
  "log_loss": 0.7084,
  "ece": 0.1909,
  "reliability": [
   {
    "bin": 0,
    "n": 25,
    "mean_pred": 0.0853,
    "observed": 0.2
   },
   {
    "bin": 1,
    "n": 24,
    "mean_pred": 0.1789,
    "observed": 0.375
   },
   {
    "bin": 2,
    "n": 25,
    "mean_pred": 0.2326,
    "observed": 0.48
   },
   {
    "bin": 3,
    "n": 24,
    "mean_pred": 0.3051,
    "observed": 0.375
   },
   {
    "bin": 4,
    "n": 25,
    "mean_pred": 0.4487,
    "observed": 0.2
   }
  ],
  "mean_value_selected": 0.6917,
  "mean_value_all": 0.7963
 },
 {
  "fold": "wf2",
  "train_n": 424,
  "tau": 0.6,
  "n": 524,
  "base_rate": 0.2863,
  "auc": 0.5004,
  "brier": 0.2574,
  "log_loss": 0.7328,
  "ece": 0.207,
  "reliability": [
   {
    "bin": 0,
    "n": 105,
    "mean_pred": 0.3227,
    "observed": 0.3238
   },
   {
    "bin": 1,
    "n": 105,
    "mean_pred": 0.4182,
    "observed": 0.2667
   },
   {
    "bin": 2,
    "n": 104,
    "mean_pred": 0.4714,
    "observed": 0.2404
   },
   {
    "bin": 3,
    "n": 105,
    "mean_pred": 0.5345,
    "observed": 0.2762
   },
   {
    "bin": 4,
    "n": 105,
    "mean_pred": 0.6622,
    "observed": 0.3238
   }
  ],
  "mean_value_selected": 0.8335,
  "mean_value_all": 0.7537
 },
 {
  "fold": "wf3",
  "train_n": 571,
  "tau": 0.5,
  "n": 469,
  "base_rate": 0.2793,
  "auc": 0.5409,
  "brier": 0.2018,
  "log_loss": 0.5924,
  "ece": 0.0757,
  "reliability": [
   {
    "bin": 0,
    "n": 94,
    "mean_pred": 0.212,
    "observed": 0.1915
   },
   {
    "bin": 1,
    "n": 94,
    "mean_pred": 0.2567,
    "observed": 0.2553
   },
   {
    "bin": 2,
    "n": 93,
    "mean_pred": 0.2901,
    "observed": 0.3978
   },
   {
    "bin": 3,
    "n": 94,
    "mean_pred": 0.3203,
    "observed": 0.2553
   },
   {
    "bin": 4,
    "n": 94,
    "mean_pred": 0.3783,
    "observed": 0.2979
   }
  ],
  "mean_value_selected": 1.0692,
  "mean_value_all": 0.8462
 },
 {
  "fold": "wf4",
  "train_n": 1181,
  "tau": 0.4,
  "n": 144,
  "base_rate": 0.2431,
  "auc": 0.5636,
  "brier": 0.1987,
  "log_loss": 0.5886,
  "ece": 0.1078,
  "reliability": [
   {
    "bin": 0,
    "n": 29,
    "mean_pred": 0.2562,
    "observed": 0.1724
   },
   {
    "bin": 1,
    "n": 29,
    "mean_pred": 0.2981,
    "observed": 0.2414
   },
   {
    "bin": 2,
    "n": 28,
    "mean_pred": 0.3229,
    "observed": 0.2857
   },
   {
    "bin": 3,
    "n": 29,
    "mean_pred": 0.3448,
    "observed": 0.1724
   },
   {
    "bin": 4,
    "n": 29,
    "mean_pred": 0.437,
    "observed": 0.3448
   }
  ],
  "mean_value_selected": 0.9644,
  "mean_value_all": 0.8317
 }
]
```

## E8 — Valor de continuación y agente de decisión frente al Champion

Versión 0.2.0, generado 2026-11-26T18:39:48+00:00, 224.2 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| champion strict+trailing | 155 | 200 | 389 | 267 | -0.0921 | 0.5206 | 69 | 27 | 0.6685 | 0.7282 |
| strict+hold | 18.89 | 200 | 200 | 0 | -0.5782 | — | 0 | 0 | 0.0037 | 0.2146 |
| strict+recovery x2 + 10% residual + trailing | 121.4 | 200 | 389 | 335 | -0.2248 | — | 0 | 0 | 0.5621 | 0.6445 |
| agent h=1h | 136.8 | 200 | 389 | 496 | -0.1848 | 0.5367 | 23 | 15 | 0.5595 | 0.7513 |
| agent h=1h no partial | 127.9 | 200 | 389 | 261 | -0.1812 | 0.5057 | 44 | 30 | 0.6564 | 0.6989 |
| agent h=30m | 112.1 | 200 | 389 | 468 | -0.2591 | 0.4599 | 29 | 12 | 0.46 | 0.691 |
| agent h=1h strict thresholds | 79.53 | 200 | 389 | 329 | -0.3106 | 0.6484 | 38 | 30 | 0.3211 | 0.5348 |

### statistical_out_of_sample

```json
{
 "1800": [
  {
   "fold": "wf0",
   "train_n": 2702,
   "n": 4981,
   "base_rate": 0.3461,
   "auc": 0.6804,
   "brier": 0.209,
   "log_loss": 0.601,
   "ece": 0.0567,
   "reliability": [
    {
     "bin": 0,
     "n": 996,
     "mean_pred": 0.0973,
     "observed": 0.0904
    },
    {
     "bin": 1,
     "n": 996,
     "mean_pred": 0.1934,
     "observed": 0.2651
    },
    {
     "bin": 2,
     "n": 997,
     "mean_pred": 0.341,
     "observed": 0.4132
    },
    {
     "bin": 3,
     "n": 995,
     "mean_pred": 0.4876,
     "observed": 0.4794
    },
    {
     "bin": 4,
     "n": 997,
     "mean_pred": 0.5929,
     "observed": 0.4824
    }
   ]
  },
  {
   "fold": "wf1",
   "train_n": 7025,
   "n": 3708,
   "base_rate": 0.308,
   "auc": 0.6929,
   "brier": 0.1944,
   "log_loss": 0.5675,
   "ece": 0.046,
   "reliability": [
    {
     "bin": 0,
     "n": 742,
     "mean_pred": 0.0653,
     "observed": 0.0714
    },
    {
     "bin": 1,
     "n": 741,
     "mean_pred": 0.1953,
     "observed": 0.2335
    },
    {
     "bin": 2,
     "n": 742,
     "mean_pred": 0.3181,
     "observed": 0.3625
    },
    {
     "bin": 3,
     "n": 741,
     "mean_pred": 0.4558,
     "observed": 0.3954
    },
    {
     "bin": 4,
     "n": 742,
     "mean_pred": 0.5493,
     "observed": 0.4771
    }
   ]
  },
  {
   "fold": "wf2",
   "train_n": 12034,
   "n": 6373,
   "base_rate": 0.3146,
   "auc": 0.6574,
   "brier": 0.2028,
   "log_loss": 0.59,
   "ece": 0.0309,
   "reliability": [
    {
     "bin": 0,
     "n": 1275,
     "mean_pred": 0.1295,
     "observed": 0.1169
    },
    {
     "bin": 1,
     "n": 1274,
     "mean_pred": 0.2515,
     "observed": 0.2739
    },
    {
     "bin": 2,
     "n": 1275,
     "mean_pred": 0.3436,
     "observed": 0.3388
    },
    {
     "bin": 3,
     "n": 1274,
     "mean_pred": 0.4436,
     "observed": 0.3689
    },
    {
     "bin": 4,
     "n": 1275,
     "mean_pred": 0.505,
     "observed": 0.4745
    }
   ]
  },
  {
   "fold": "wf3",
   "train_n": 14483,
   "n": 8618,
   "base_rate": 0.2965,
   "auc": 0.6625,
   "brier": 0.1961,
   "log_loss": 0.5746,
   "ece": 0.0362,
   "reliability": [
    {
     "bin": 0,
     "n": 1724,
     "mean_pred": 0.0916,
     "observed": 0.0916
    },
    {
     "bin": 1,
     "n": 1723,
     "mean_pred": 0.2159,
     "observed": 0.2403
    },
    {
     "bin": 2,
     "n": 1724,
     "mean_pred": 0.309,
     "observed": 0.3324
    },
    {
     "bin": 3,
     "n": 1723,
     "mean_pred": 0.4163,
     "observed": 0.4016
    },
    {
     "bin": 4,
     "n": 1724,
     "mean_pred": 0.5106,
     "observed": 0.4165
    }
   ]
  },
  {
   "fold": "wf4",
   "train_n": 23597,
   "n": 2695,
   "base_rate": 0.3763,
   "auc": 0.6051,
   "brier": 0.2276,
   "log_loss": 0.6481,
   "ece": 0.054,
   "reliability": [
    {
     "bin": 0,
     "n": 539,
     "mean_pred": 0.1644,
     "observed": 0.2263
    },
    {
     "bin": 1,
     "n": 539,
     "mean_pred": 0.2822,
     "observed": 0.3469
    },
    {
     "bin": 2,
     "n": 539,
     "mean_pred": 0.3535,
     "observed": 0.3803
    },
    {
     "bin": 3,
     "n": 539,
     "mean_pred": 0.4384,
     "observed": 0.4917
    },
    {
     "bin": 4,
     "n": 539,
     "mean_pred": 0.4992,
     "observed": 0.436
    }
   ]
  }
 ],
 "3600": [
  {
   "fold": "wf0",
   "train_n": 2670,
   "n": 4965,
   "base_rate": 0.3507,
   "auc": 0.6175,
   "brier": 0.2308,
   "log_loss": 0.668,
   "ece": 0.0889,
   "reliability": [
    {
     "bin": 0,
     "n": 993,
     "mean_pred": 0.1292,
     "observed": 0.1994
    },
    {
     "bin": 1,
     "n": 993,
     "mean_pred": 0.2101,
     "observed": 0.2961
    },
    {
     "bin": 2,
     "n": 993,
     "mean_pred": 0.3009,
     "observed": 0.3515
    },
    {
     "bin": 3,
     "n": 993,
     "mean_pred": 0.4516,
     "observed": 0.4642
    },
    {
     "bin": 4,
     "n": 993,
     "mean_pred": 0.6277,
     "observed": 0.4421
    }
   ]
  },
  {
   "fold": "wf1",
   "train_n": 6973,
   "n": 3687,
   "base_rate": 0.3214,
   "auc": 0.6376,
   "brier": 0.21,
   "log_loss": 0.6057,
   "ece": 0.0654,
   "reliability": [
    {
     "bin": 0,
     "n": 738,
     "mean_pred": 0.113,
     "observed": 0.1125
    },
    {
     "bin": 1,
     "n": 737,
     "mean_pred": 0.2421,
     "observed": 0.3148
    },
    {
     "bin": 2,
     "n": 737,
     "mean_pred": 0.3152,
     "observed": 0.3677
    },
    {
     "bin": 3,
     "n": 737,
     "mean_pred": 0.4173,
     "observed": 0.3501
    },
    {
     "bin": 4,
     "n": 738,
     "mean_pred": 0.57,
     "observed": 0.4621
    }
   ]
  },
  {
   "fold": "wf2",
   "train_n": 11952,
   "n": 6366,
   "base_rate": 0.3021,
   "auc": 0.5978,
   "brier": 0.2081,
   "log_loss": 0.6102,
   "ece": 0.0408,
   "reliability": [
    {
     "bin": 0,
     "n": 1273,
     "mean_pred": 0.1707,
     "observed": 0.1885
    },
    {
     "bin": 1,
     "n": 1273,
     "mean_pred": 0.2857,
     "observed": 0.2836
    },
    {
     "bin": 2,
     "n": 1274,
     "mean_pred": 0.3254,
     "observed": 0.2975
    },
    {
     "bin": 3,
     "n": 1272,
     "mean_pred": 0.3922,
     "observed": 0.3294
    },
    {
     "bin": 4,
     "n": 1274,
     "mean_pred": 0.4802,
     "observed": 0.4113
    }
   ]
  },
  {
   "fold": "wf3",
   "train_n": 14382,
   "n": 8564,
   "base_rate": 0.2898,
   "auc": 0.5896,
   "brier": 0.2057,
   "log_loss": 0.6017,
   "ece": 0.0567,
   "reliability": [
    {
     "bin": 0,
     "n": 1713,
     "mean_pred": 0.1367,
     "observed": 0.1646
    },
    {
     "bin": 1,
     "n": 1713,
     "mean_pred": 0.2611,
     "observed": 0.2574
    },
    {
     "bin": 2,
     "n": 1712,
     "mean_pred": 0.3054,
     "observed": 0.3388
    },
    {
     "bin": 3,
     "n": 1713,
     "mean_pred": 0.3803,
     "observed": 0.3433
    },
    {
     "bin": 4,
     "n": 1713,
     "mean_pred": 0.4816,
     "observed": 0.345
    }
   ]
  },
  {
   "fold": "wf4",
   "train_n": 23480,
   "n": 2578,
   "base_rate": 0.3445,
   "auc"
```

### counterfactual

```json
{
 "closed": 267,
 "with_post_exit_data": 267,
 "median_best_after_exit_vs_exit_price": 1.5063,
 "share_doubling_after_exit": 0.3146,
 "note": "gross price path after the exit (no costs); retrospective, never a decision input"
}
```

## E9 — Validación: carteras A–I walk-forward, bootstrap por bloques, Holm, costes, tickets y frecuencia

Versión 0.2.0, generado 2026-11-26T18:51:43+00:00, 715.0 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| A random tradable | 168 | 200 | 115 | 60 | -0.1693 | 0.4667 | 17 | 9 | 0.6275 | 0.8693 |
| B random + basic security | 176.3 | 200 | 79 | 45 | -0.2963 | 0.5111 | 12 | 5 | 0.6331 | 0.9322 |
| C economic + security | 164.2 | 200 | 243 | 153 | -0.1137 | 0.4837 | 38 | 25 | 0.6371 | 0.7511 |
| D existing heuristic | 200 | 200 | 0 | 0 | — | — | 0 | 0 | — | 1 |
| E supervised selection | 157.9 | 200 | 226 | 155 | -0.1768 | 0.5355 | 36 | 15 | 0.6847 | 0.781 |
| F supervised + contextual monetisation | 148 | 200 | 213 | 181 | -0.2938 | — | 0 | 0 | 0.5576 | 0.7377 |
| G supervised + decision agent | 163.3 | 200 | 228 | 298 | -0.1747 | 0.4296 | 20 | 13 | 0.5379 | 0.8082 |
| H agent + compounding | 94.23 | 200 | 646 | 874 | -0.1845 | 0.4025 | 39 | 28 | 0.5898 | 0.5924 |
| I agent + compounding + second chances | 49.79 | 200 | 876 | 1228 | -0.1867 | 0.3783 | 56 | 38 | 0.5706 | 0.4054 |
| Champion strict+trailing (paced) | 177.9 | 200 | 256 | 179 | -0.0577 | 0.5531 | 48 | 17 | 0.6689 | 0.7963 |
| Champion strict+trailing (compounding) | 155 | 200 | 389 | 267 | -0.0921 | 0.5206 | 69 | 27 | 0.6685 | 0.7282 |

### Sensibilidad: fees
| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| fee flat_30bps_legacy_assumption | 159.6 | 200 | 389 | 269 | -0.0777 | 0.5279 | 71 | 28 | 0.669 | 0.7307 |
| fee pumpswap_measured_2026_10 | 155 | 200 | 389 | 267 | -0.0921 | 0.5206 | 69 | 27 | 0.6685 | 0.7282 |
| fee flat_125bps_conservative | 154.6 | 200 | 389 | 267 | -0.0932 | 0.5169 | 70 | 27 | 0.6693 | 0.7279 |

### Sensibilidad: adverse
| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| adverse 0 bps | 154 | 200 | 389 | 267 | -0.0951 | 0.5131 | 71 | 27 | 0.6609 | 0.7275 |
| adverse 100 bps | 155 | 200 | 389 | 267 | -0.0921 | 0.5206 | 69 | 27 | 0.6685 | 0.7282 |
| adverse 300 bps | 152.6 | 200 | 389 | 262 | -0.0973 | 0.542 | 66 | 27 | 0.6744 | 0.7212 |
| adverse 600 bps | 149.4 | 200 | 389 | 255 | -0.1041 | 0.5647 | 62 | 27 | 0.6901 | 0.6971 |

### Sensibilidad: tickets
| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| ticket 0.5 EUR | 173.1 | 200 | 389 | 241 | -0.1579 | 0.5975 | 53 | 23 | 0.6918 | 0.8421 |
| ticket 1 EUR | 155 | 200 | 389 | 267 | -0.0921 | 0.5206 | 69 | 27 | 0.6685 | 0.7282 |
| ticket 2 EUR | 117.1 | 200 | 389 | 279 | -0.0684 | 0.491 | 74 | 28 | 0.6497 | 0.5214 |
| ticket 5 EUR | 24.63 | 200 | 198 | 146 | -0.147 | 0.4795 | 41 | 11 | 0.6279 | 0.2005 |

### Launch Tape (fuera de periodo, ya explorado: no independiente)
| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| strict+trailing @5s | 91.96 | 100 | 21 | 7 | -0.4412 | 0.2857 | 0 | 0 | 0.2476 | 0.9029 |
| basic+trailing @5s | 75.66 | 100 | 65 | 26 | -0.4201 | 0.3077 | 2 | 0 | 0.4201 | 0.7103 |
| strict+trailing @15s | 91.37 | 100 | 21 | 6 | -0.4789 | 0 | 0 | 0 | 0.2451 | 0.8986 |
| basic+trailing @15s | 77.89 | 100 | 65 | 23 | -0.3925 | 0.3478 | 3 | 0 | 0.3806 | 0.6971 |
| strict+trailing @60s | 90.9 | 100 | 18 | 4 | -0.577 | 0 | 0 | 0 | 0.1032 | 0.9078 |
| basic+trailing @60s | 76.73 | 100 | 62 | 17 | -0.441 | 0.1765 | 2 | 0 | 0.2934 | 0.6833 |
| strict+trailing latency 2s | 91.37 | 100 | 21 | 6 | -0.4789 | 0 | 0 | 0 | 0.2451 | 0.8986 |
| strict+trailing latency 10s | 91.53 | 100 | 21 | 6 | -0.47 | 0.1667 | 0 | 0 | 0.2514 | 0.8987 |
| strict+trailing latency 30s | 91.42 | 100 | 21 | 6 | -0.4778 | 0.1667 | 0 | 0 | 0.2574 | 0.8987 |

### holm

```json
{
 "D existing heuristic": {
  "p": 0.11,
  "threshold": 0.001,
  "significant": false
 },
 "B random + basic security": {
  "p": 0.4876,
  "threshold": 0.00102,
  "significant": false
 },
 "A random tradable": {
  "p": 0.71,
  "threshold": 0.00104,
  "significant": false
 },
 "G supervised + decision agent": {
  "p": 0.7814,
  "threshold": 0.00106,
  "significant": false
 },
 "C economic + security": {
  "p": 0.8484,
  "threshold": 0.00109,
  "significant": false
 },
 "E supervised selection": {
  "p": 0.881,
  "threshold": 0.00111,
  "significant": false
 },
 "F supervised + contextual monetisation": {
  "p": 0.9558,
  "threshold": 0.00114,
  "significant": false
 },
 "H agent + compounding": {
  "p": 0.9874,
  "threshold": 0.00116,
  "significant": false
 },
 "I agent + compounding + second chances": {
  "p": 0.9974,
  "threshold": 0.00119,
  "significant": false
 },
 "Champion strict+trailing (compounding)": {
  "p": 0.9998,
  "threshold": 0.00122,
  "significant": false
 }
}
```

## E10 — Recuperación de perdedoras y monetización: trailing, stops, salidas por tiempo, recuperación

Versión 0.2.0, generado 2026-11-26T18:36:50+00:00, 326.4 s.

| label | net_worth_liquidable_eur | contributions_eur | buys | sells | mean_return_per_ticket | win_rate_closed | peak_ge_x2 | peak_ge_x2_realised_ge_x2 | median_peak_capture | cash_idle_share |
|---|---|---|---|---|---|---|---|---|---|---|
| champion trailing 30% | 144.3 | 200 | 464 | 320 | -0.0998 | 0.5281 | 81 | 32 | 0.668 | 0.6764 |
| trailing 20% | 165 | 200 | 464 | 345 | -0.0509 | 0.5942 | 50 | 23 | 0.8144 | 0.7296 |
| trailing 45% | 89.05 | 200 | 464 | 289 | -0.2351 | 0.3737 | 106 | 24 | 0.4782 | 0.557 |
| trailing activates at x1.5 | 96.18 | 200 | 464 | 215 | -0.1902 | 0.8977 | 117 | 42 | 0.1642 | 0.4759 |
| + stop at x0.5 | 169.2 | 200 | 464 | 395 | -0.0604 | 0.3924 | 75 | 29 | 0.7016 | 0.8302 |
| + stop at x0.7 | 178 | 200 | 464 | 444 | -0.0514 | 0.2455 | 51 | 19 | 0.774 | 0.9443 |
| + time stop 6 h | 156.9 | 200 | 464 | 345 | -0.0848 | 0.4986 | 77 | 31 | 0.6868 | 0.729 |
| + time stop 24 h | 152.4 | 200 | 464 | 339 | -0.0949 | 0.5015 | 81 | 32 | 0.6696 | 0.7101 |
| + recovery at x2, keep 10% | 103.7 | 200 | 464 | 399 | -0.2364 | — | 0 | 0 | 0.5623 | 0.5643 |
| + recovery at x1.5, keep 25% | 69.02 | 200 | 464 | 450 | -0.2736 | 0.3636 | 12 | 5 | 0.4995 | 0.4683 |
