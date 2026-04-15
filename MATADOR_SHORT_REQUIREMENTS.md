# Short Customer Requirements for DIFRA -> Matador Data Storage

These are high-level customer requirements from the DIFRA side.
The goal is not to change the current acquisition workflow, but to ensure that Matador can ingest and process the data produced by it.

1. Matador must accept calibration and measurement data produced by DIFRA without requiring changes to the current acquisition workflow.
2. Operators should primarily work with the HDF5 container as the main user-facing artifact. The underlying raw files may remain internal or archived artifacts and may still be used for packaging and upload to Matador.
3. Measurement data for a single specimen must remain processable based on the specimen-specific `<bundle>_state.json`, which is the source of spatial and measurement context for that specimen.
4. DIFRA must be able to upload data in small session-based batches: calibration data plus one specimen measurement at a time.
5. DIFRA may upload the same calibration data repeatedly together with different specimen measurements. Matador must handle such repeated calibration uploads idempotently: if an equivalent calibration has already been ingested, it should be reused or recognized as duplicate rather than creating additional calibration records. DIFRA must not depend on Matador's internal database state, prior ingestion status, or pre-check calls before upload.
6. After successful ingestion, Matador must process the uploaded calibration and measurement data and make them available in its database in the same way as in the previous folder-based workflow.

These requirements are intentionally minimal and customer-facing. Detailed ZIP structure, JSON schemas, and API behavior should remain aligned with the current Matador contract.
