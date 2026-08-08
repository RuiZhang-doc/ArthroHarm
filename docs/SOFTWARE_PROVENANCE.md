# Software provenance

## Evaluated version

The Stage II evaluation used ArthroHarm v1.2. The core files in this public release are byte-identical to the locked files retained in the Stage II capsule or, for the one-time wrapper and reproducibility verifier, to their authoritative post-freeze audit copies.

| File | SHA-256 |
|---|---|
| `arthroharm_extract_v1_1.py` | `96a1d732814b8e7e14cf072c2ef4f77080a3130b7da0bd51406acde6ad9ee4bf` |
| `arthroharm_extract_v1_2.py` | `57ee1eaadeaa7d27ed79d4e21e5658204b1e64ece24976707302cb473e6cde2f` |
| `arthroharm_rules_v1.0.json` | `972f7055946f723d3fe32c965b73e8fab307cffddeb9634d9b3325c67087ee71` |
| `arthroharm_rules_v1.1.json` | `dffbef0cf04f5710f8be07c42640b7475d9b133c8047c93389321139140e2b9a` |
| `arthroharm_rules_v1.2.json` | `c29c22f1615472562c82516f979e2c3003a1142ba62b8edd338df0b21333bb2b` |
| `normalize_publisher_fulltext_v1_2.py` | `23e07116260abe52a2d2a48d9f67f0150500cdc598176a2a9a4ddb5949fa383c` |
| `normalize_publisher_pdf_v1_2.py` | `5fe3fc69bf2fe622ffecdb20d20a709abdad3de6f935723ff5c40bdda43fb5df` |
| `arthroharm_evaluation_v1_2.py` | `e6f01b54f6bd35558ce970fb298cca2c650b16a6e342e1c365bab47f41de80de` |
| `run_stage2_external_scoring_v1_2.py` | `223f2fdf18009d3ea3362d13dd0bb8bdef490dee1f2b47b21bd0fc354e8c4305` |
| `freeze_stage2_external_v1_2.py` | `91ea7616ffadce54cf14313090bb6e9dca15927a26fb649aab6cfd3d0e1d68b7` |
| `seal_stage2_predictions_v1_2.py` | `4728386b3962280a31bff90970dc3ae501704f9aa476973b641886e8aaa948a8` |
| `verify_stage2_capsule_reproducibility_v1_2.py` | `599faf781a0aded80666971b07f05591c1533ecffd78534a57adb1a158a3403e` |

The release-wide `MANIFEST.sha256` is generated only after every public file is finalized. It is distinct from the Stage II capsule manifest and does not overwrite any original artifact.

## Scientific boundary

- Development and Stage I informed rule development and are not presented as v1.2 external validation.
- Stage II was temporally and full-text-source independent from development and used a frozen candidate set, sealed predictions, dual blinded clinical annotation, adjudication and one-time scoring.
- A later human semantic matcher audit is reported as a post hoc sensitivity analysis and does not replace the frozen automatic-matcher primary result.

