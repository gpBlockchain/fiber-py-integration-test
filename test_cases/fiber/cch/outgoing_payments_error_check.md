## Summary

Improve permanent error detection in CCH outgoing payment executors so that unrecoverable errors are identified immediately instead of being retried indefinitely.

## Problem

Both `SendFiberOutgoingPaymentExecutor` and `SendLightningOutgoingPaymentExecutor` had minimal permanent error detection. For Fiber payments, only `InvalidParameter` was recognized. For Lightning payments, only `tonic::Code::InvalidArgument` was checked. This caused unrecoverable errors to be retried repeatedly, wasting resources and delaying final failure reporting.

## Changes

### Fiber outgoing payments (`SendFiberOutgoingPaymentExecutor`)

Detect additional permanent errors beyond `InvalidParameter` via case-insensitive message matching:
- `invalid payment request`
- `invoice expired`
- `payment hash mismatch`
- `no path found`

### Lightning outgoing payments (`SendLightningOutgoingPaymentExecutor`)

LND often returns validation errors under `tonic::Code::Unknown` rather than `InvalidArgument`. Now checks the error message for these permanent failures:
- `self-payments not allowed`
- `invoice is already paid`
- `invoice expired`
- `incorrect payment amount`
- `payment hash mismatch`
- `no route`
- `unable to find a path to destination`