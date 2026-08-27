# Decision-threshold cost analysis

Cost model: FP (good order held/manual review) = 1.0 unit, FN (RTO missed, ships anyway) = 12.0 units (industry: RTO loss + reverse logistics ~10-15x review cost; methodology per
Drummond & Holte, *Machine Learning* 65:95-130, 2006 - see docs/research/INDEX.md).

| threshold | flagged | precision | recall | FP | FN | total cost |
|---|---|---|---|---|---|---|
| 0.15 | 663 | 0.406 | 0.789 | 394 | 72 | 1258.0 |
| 0.2 | 571 | 0.443 | 0.742 | 318 | 88 | 1374.0 |
| 0.25 | 505 | 0.465 | 0.689 | 270 | 106 | 1542.0 |
| 0.3 | 447 | 0.485 | 0.636 | 230 | 124 | 1718.0 |
| 0.35 | 393 | 0.506 | 0.584 | 194 | 142 | 1898.0 |
| 0.4 | 338 | 0.533 | 0.528 | 158 | 161 | 2090.0 |
| 0.5 | 229 | 0.581 | 0.39 | 96 | 208 | 2592.0 |
| 0.6 | 146 | 0.651 | 0.279 | 51 | 246 | 3003.0 |

**Cost-optimal threshold: 0.15** (cost 1258.0 units)
