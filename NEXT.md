687-r1 regression tests were added before the fix and run against 81f1bfd2513010514c90b72d51e4d0a3b000c38e.
Expected failures captured from the targeted unittest run:
- test_transfer_successor_cannot_name_an_older_landed_receipt: AssertionError errors was empty.
- test_malformed_transfer_successor_is_refused: AssertionError errors was empty.
- test_historical_assets_must_be_a_list_of_full_digests: AssertionError errors was empty.
- test_transfer_successor_is_framework_data: successor absent from FRAMEWORK_DATA_FILES.
The partial archive SHA test already failed closed on the current code.
Control checks after fix commit:
- Replaced successor validation with the pre-fix permissive lookup. The older-receipt and malformed-successor tests failed (2 failures).
- Replaced historical_skia_assets validation with the pre-fix membership test. The malformed-list/partial-digest test failed.
- Removed transfer-successor.json from FRAMEWORK_DATA_FILES. The data-file-list test failed.
Each temporary edit was restored from the fixed file after its run.
