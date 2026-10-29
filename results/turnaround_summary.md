# Command turnaround: FIFO vs priority + retry (simulated)

Command: `python sim/run_turnaround.py --days 55 --seeds 3 --loads 0.25 0.4 0.55 0.7`

- Contact plan: 1162 predicted passes over 55 days at 3 stations (toronto: 187, svalbard: 707, punta_arenas: 235 scheduled), 1129 scheduled contacts after de-confliction, 139.8 h of contact.
- Contact duration median 7.7 min, max elevation median 34.5 deg.
- Link: 2 s per TC frame, ACK timeout 6 s, frame loss from a slant-range/elevation margin model with per-pass fading.
- Turnaround = submission to ACK received, in minutes. Values are means over seeds of per-run statistics; commands submitted in the final 24 h are excluded.

## Offered load 0.25

| Class | Policy | Submitted | ACKed | Failed | Pending | Mean (min) | P50 (min) | P95 (min) | Attempts/cmd |
|---|---|---|---|---|---|---|---|---|---|
| ALL | fifo | 27041 | 27041 | 0 | 0 | 67.7 | 47.6 | 218.9 | 1.68 |
| ALL | priority_retry | 27041 | 27021 | 20 | 0 | 60.9 | 43.6 | 200.0 | 1.91 |
| CRITICAL | fifo | 2713 | 2713 | 0 | 0 | 60.4 | 46.6 | 185.4 | 1.54 |
| CRITICAL | priority_retry | 2713 | 2693 | 20 | 0 | 70.3 | 47.8 | 234.0 | 3.41 |
| HIGH | fifo | 5363 | 5363 | 0 | 0 | 62.2 | 46.9 | 191.5 | 1.52 |
| HIGH | priority_retry | 5363 | 5363 | 0 | 0 | 53.7 | 41.0 | 164.6 | 2.20 |
| NORMAL | fifo | 13626 | 13626 | 0 | 0 | 62.1 | 45.3 | 196.7 | 1.58 |
| NORMAL | priority_retry | 13626 | 13626 | 0 | 0 | 49.9 | 39.1 | 152.9 | 1.59 |
| BULK | fifo | 5339 | 5339 | 0 | 0 | 91.1 | 57.4 | 296.2 | 2.15 |
| BULK | priority_retry | 5339 | 5339 | 0 | 0 | 91.3 | 60.1 | 273.8 | 1.66 |

Duplicate executions on board (all seeds): fifo 2151, priority_retry 1288.

## Offered load 0.4

| Class | Policy | Submitted | ACKed | Failed | Pending | Mean (min) | P50 (min) | P95 (min) | Attempts/cmd |
|---|---|---|---|---|---|---|---|---|---|
| ALL | fifo | 43331 | 43331 | 0 | 0 | 104.9 | 75.1 | 303.2 | 1.49 |
| ALL | priority_retry | 43331 | 43326 | 5 | 0 | 70.1 | 47.1 | 237.0 | 1.61 |
| CRITICAL | fifo | 4289 | 4289 | 0 | 0 | 93.1 | 70.7 | 257.4 | 1.33 |
| CRITICAL | priority_retry | 4289 | 4284 | 5 | 0 | 61.6 | 42.4 | 207.8 | 2.64 |
| HIGH | fifo | 8642 | 8642 | 0 | 0 | 92.5 | 70.5 | 252.2 | 1.33 |
| HIGH | priority_retry | 8642 | 8642 | 0 | 0 | 50.2 | 38.6 | 152.0 | 1.79 |
| NORMAL | fifo | 21752 | 21752 | 0 | 0 | 98.7 | 73.1 | 281.0 | 1.40 |
| NORMAL | priority_retry | 21752 | 21752 | 0 | 0 | 53.6 | 42.0 | 161.2 | 1.36 |
| BULK | fifo | 8648 | 8648 | 0 | 0 | 139.0 | 90.9 | 428.3 | 1.93 |
| BULK | priority_retry | 8648 | 8648 | 0 | 0 | 136.0 | 98.5 | 357.4 | 1.56 |

Duplicate executions on board (all seeds): fifo 2411, priority_retry 1331.

## Offered load 0.55

| Class | Policy | Submitted | ACKed | Failed | Pending | Mean (min) | P50 (min) | P95 (min) | Attempts/cmd |
|---|---|---|---|---|---|---|---|---|---|
| ALL | fifo | 59661 | 59616 | 0 | 45 | 582.7 | 474.7 | 1397.8 | 1.41 |
| ALL | priority_retry | 59661 | 59659 | 2 | 0 | 239.7 | 126.1 | 790.0 | 1.45 |
| CRITICAL | fifo | 5994 | 5992 | 0 | 2 | 530.6 | 454.4 | 1214.5 | 1.28 |
| CRITICAL | priority_retry | 5994 | 5992 | 2 | 0 | 59.3 | 41.9 | 195.1 | 1.94 |
| HIGH | fifo | 11845 | 11842 | 0 | 3 | 524.3 | 449.6 | 1215.7 | 1.27 |
| HIGH | priority_retry | 11845 | 11845 | 0 | 0 | 93.3 | 51.6 | 338.7 | 1.42 |
| NORMAL | fifo | 29799 | 29783 | 0 | 16 | 546.6 | 460.7 | 1285.1 | 1.33 |
| NORMAL | priority_retry | 29799 | 29799 | 0 | 0 | 220.9 | 136.2 | 668.4 | 1.28 |
| BULK | fifo | 12023 | 11999 | 0 | 24 | 755.8 | 564.4 | 1994.8 | 1.84 |
| BULK | priority_retry | 12023 | 12023 | 0 | 0 | 520.0 | 483.2 | 1057.4 | 1.65 |

Duplicate executions on board (all seeds): fifo 2848, priority_retry 1353.

## Offered load 0.7

| Class | Policy | Submitted | ACKed | Failed | Pending | Mean (min) | P50 (min) | P95 (min) | Attempts/cmd |
|---|---|---|---|---|---|---|---|---|---|
| ALL | fifo | 75981 | 63709 | 0 | 12272 | 6794.2 | 6104.6 | 17285.3 | 1.18 |
| ALL | priority_retry | 75981 | 61290 | 1 | 14690 | 8091.4 | 8003.7 | 15882.5 | 1.12 |
| CRITICAL | fifo | 7719 | 6568 | 0 | 1151 | 6439.3 | 5972.5 | 15780.6 | 1.09 |
| CRITICAL | priority_retry | 7719 | 6274 | 0 | 1445 | 7442.2 | 7352.2 | 15244.4 | 1.06 |
| HIGH | fifo | 15068 | 12809 | 0 | 2259 | 6345.1 | 5834.4 | 15514.2 | 1.09 |
| HIGH | priority_retry | 15068 | 12183 | 0 | 2885 | 7743.8 | 7573.1 | 15543.1 | 1.03 |
| NORMAL | fifo | 37960 | 32226 | 0 | 5734 | 6566.6 | 5970.0 | 16388.0 | 1.13 |
| NORMAL | priority_retry | 37960 | 30653 | 0 | 7307 | 8161.2 | 8015.4 | 15940.7 | 1.06 |
| BULK | fifo | 15234 | 12106 | 0 | 3128 | 8067.3 | 6838.0 | 20921.0 | 1.45 |
| BULK | priority_retry | 15234 | 12180 | 1 | 3053 | 8597.4 | 8533.6 | 16306.5 | 1.39 |

Duplicate executions on board (all seeds): fifo 2575, priority_retry 1053.

Total wall time: 67 s.
