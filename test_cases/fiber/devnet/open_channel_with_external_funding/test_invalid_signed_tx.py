import re

import pytest

from test_cases.fiber.devnet.open_channel_with_external_funding.external_funding_base import (
    ExternalFundingBase,
)


class TestExternalFundingInvalidSignedTx(ExternalFundingBase):
    """
    PR-1120 invalid signed transaction and state-check coverage.

    Mapped doc cases:
    - T-05/T-06/T-07/T-08/T-22/T-23 tampered funding transaction validation
    - T-09/T-10/T-11 invalid submit_signed_funding_tx state checks
    """

    __test__ = True

    @staticmethod
    def _assert_raw_data_mismatch(error_message, *legacy_markers):
        if "Signed funding transaction raw data mismatch" in error_message:
            return
        for marker in legacy_markers:
            if marker in error_message:
                return
        raise AssertionError(
            "Expected a raw-data mismatch style error, "
            f"got '{error_message}' instead"
        )

    def _submit_tampered_tx_and_get_error(self, tamper_fn):
        """Shared helper for T-05 to T-07 style invalid signed transaction tests."""
        context = self._open_external_funding_channel(public=True)
        tampered_tx = self._clone_tx(context["unsigned_funding_tx"])
        tamper_fn(tampered_tx)
        signed_tampered_tx = self._sign_external_funding_tx(
            tampered_tx, context["external_private_key"]
        )

        with pytest.raises(Exception) as exc_info:
            self._submit_external_funding(context["channel_id"], signed_tampered_tx)
        return exc_info.value.args[0]

    def test_output_mismatch(self):
        """T-05: outputs modified from unsigned_funding_tx should be rejected."""

        def tamper(tx):
            tx["outputs"][0]["capacity"] = hex(
                int(tx["outputs"][0]["capacity"], 16) + 1
            )

        error_message = self._submit_tampered_tx_and_get_error(tamper)
        self._assert_raw_data_mismatch(error_message, "Output 0 mismatch")

    def test_input_count_mismatch(self):
        """T-06: input count mismatch against the original unsigned tx should fail."""

        def tamper(tx):
            tx["inputs"].append(self._clone_tx(tx["inputs"][0]))

        error_message = self._submit_tampered_tx_and_get_error(tamper)
        if "Signed funding transaction raw data mismatch" not in error_message:
            error_pattern = r"Input count mismatch: unsigned has \d+, signed has \d+"
            assert re.search(error_pattern, error_message), (
                f"Expected pattern '{error_pattern}' "
                f"not found in actual string '{error_message}'"
            )

    def test_output_data_mismatch(self):
        """T-07: outputs_data tampering should be rejected as a mismatch."""

        def tamper(tx):
            tx["outputs_data"][0] = (
                "0x01" if tx["outputs_data"][0] in ("0x", "0x0") else "0x"
            )

        error_message = self._submit_tampered_tx_and_get_error(tamper)
        self._assert_raw_data_mismatch(error_message, "Output data 0 mismatch")

    def test_previous_output_mismatch(self):
        """
        T-08: previous_output must remain identical to the unsigned template.

        We sign the original unsigned tx first, then tamper the signed copy, so
        the submit path validates the mismatch instead of failing earlier in the
        local signing helper.
        """
        context = self._open_external_funding_channel(public=True)
        signed_funding_tx = self._sign_external_funding_tx(
            context["unsigned_funding_tx"], context["external_private_key"]
        )
        tampered_signed_tx = self._clone_tx(signed_funding_tx)
        current_index = int(
            tampered_signed_tx["inputs"][0]["previous_output"]["index"], 16
        )
        tampered_signed_tx["inputs"][0]["previous_output"]["index"] = hex(
            current_index + 1
        )

        with pytest.raises(Exception) as exc_info:
            self._submit_external_funding(context["channel_id"], tampered_signed_tx)

        error_message = exc_info.value.args[0]
        self._assert_raw_data_mismatch(
            error_message, "Input 0 previous_output mismatch"
        )

    def test_output_count_mismatch(self):
        """
        T-22: outputs count must remain identical to the unsigned template.

        We tamper the already signed transaction copy so the submit path hits the
        output-count validation directly.
        """
        context = self._open_external_funding_channel(public=True)
        signed_funding_tx = self._sign_external_funding_tx(
            context["unsigned_funding_tx"], context["external_private_key"]
        )
        tampered_signed_tx = self._clone_tx(signed_funding_tx)
        tampered_signed_tx["outputs"].pop()
        tampered_signed_tx["outputs_data"].pop()

        with pytest.raises(Exception) as exc_info:
            self._submit_external_funding(context["channel_id"], tampered_signed_tx)

        error_message = exc_info.value.args[0]
        if "Signed funding transaction raw data mismatch" not in error_message:
            error_pattern = r"Output count mismatch: unsigned has \d+, signed has \d+"
            assert re.search(error_pattern, error_message), (
                f"Expected pattern '{error_pattern}' "
                f"not found in actual string '{error_message}'"
            )

    def test_outputs_data_count_mismatch(self):
        """
        T-23: outputs_data count must stay aligned with outputs.

        This specifically targets array-length mismatch rather than content
        mismatch, so we only change outputs_data length.
        """
        context = self._open_external_funding_channel(public=True)
        signed_funding_tx = self._sign_external_funding_tx(
            context["unsigned_funding_tx"], context["external_private_key"]
        )
        tampered_signed_tx = self._clone_tx(signed_funding_tx)
        tampered_signed_tx["outputs_data"].pop()

        with pytest.raises(Exception) as exc_info:
            self._submit_external_funding(context["channel_id"], tampered_signed_tx)

        error_message = exc_info.value.args[0]
        if "Signed funding transaction raw data mismatch" not in error_message:
            error_pattern = (
                r"Outputs data count mismatch: unsigned has \d+, signed has \d+"
            )
            assert re.search(error_pattern, error_message), (
                f"Expected pattern '{error_pattern}' "
                f"not found in actual string '{error_message}'"
            )

    def test_submit_signed_funding_tx_on_normal_channel(self):
        """
        T-09: submit_signed_funding_tx only applies to channels waiting for
        external funding, not to ordinary open_channel flows.
        """
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 0)
        normal_channel_id = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"][0]["channel_id"]
        external_context = self._open_external_funding_channel(public=True)
        signed_funding_tx = self._sign_external_funding_tx(
            external_context["unsigned_funding_tx"],
            external_context["external_private_key"],
        )

        with pytest.raises(Exception, match="Invalid state:"):
            self._submit_external_funding(normal_channel_id, signed_funding_tx)

    def test_duplicate_submit_signed_funding_tx(self):
        """T-10: the same signed funding tx cannot be submitted twice."""
        context = self._open_sign_submit_external_channel(public=True)

        with pytest.raises(Exception) as exc_info:
            self._submit_external_funding(
                context["channel_id"], context["signed_funding_tx"]
            )

        expected_error_message = "Signed funding tx has already been submitted"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_submit_signed_funding_tx_with_nonexistent_channel_id(self):
        """T-11: submitting against an unknown channel_id should return an error."""
        context = self._open_external_funding_channel(public=True)
        signed_funding_tx = self._sign_external_funding_tx(
            context["unsigned_funding_tx"], context["external_private_key"]
        )
        fake_channel_id = self.generate_random_preimage()

        with pytest.raises(Exception) as exc_info:
            self._submit_external_funding(fake_channel_id, signed_funding_tx)

        error_message = exc_info.value.args[0]
        error_pattern = r"Channel not found error: Hash256\(0x[0-9a-f]{64}\)"
        assert re.search(error_pattern, error_message), (
            f"Expected pattern '{error_pattern}' "
            f"not found in actual string '{error_message}'"
        )
