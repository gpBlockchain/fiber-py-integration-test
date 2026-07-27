import pytest

from framework.basic_fiber_with_cch import FiberCchTest


# A valid, signed Fibd invoice generated with InvoiceBuilder without calling
# final_expiry_delta(). The new_invoice RPC cannot create this compatibility case:
# it always writes its own fallback delta.
MISSING_FINAL_EXPIRY_DELTA_INVOICE = "fibd10001p902j3k6qenczxzat8lhvwyrgdpgc5kru039unhcw86yuwzj3g9trkyx0njj9pg2tz384xttan0n26e3cnsznazmuu2ta09t25q9c3qwvqgwkkgzk9sgf50ckfyfqtnygy9295ekcsg5uln5jcg5l6eut70mf8wsnjdeuwqjl9zp3f7urx7n6x3xmefurce89q7p65v9gl68rs50pt0pxjs5g93xlsase4602yy984pxwxpkmdevuqw6gpgr9s0hlmmqr70y30jkehqu0uf8qwt5nu74tj4gyrxvty6p5xm9ay82xsypqnadqwnw2lph7405wp9nkfu2tcthjvc82l0e0cy79qrp57hgn5s04rnwsl7sex6evvcupgevq8jwe9u9nrhe2vagqq3g2l4nzy3fpecku8v7s3qz3e0mcqaqmjjk36j20hs3clxl50fhrsdajqlrtrgwnna6d4dz0w9t42qskee4srcla3vmzrhhcx8vrj5cqzv0qmz"
BTC_FINAL_TLC_EXPIRY_DELTA_BLOCKS = 144
RECEIVE_BTC_FINAL_TLC_ERROR = (
    "CKB invoice final TLC expiry delta exceeds safe limit for cross-chain swap"
)


class TestPr1557MissingFinalExpiryDelta(FiberCchTest):
    def _restart_cch_with_24_hour_btc_window(self):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_btc_final_tlc_expiry_delta_blocks": BTC_FINAL_TLC_EXPIRY_DELTA_BLOCKS,
            }
        )
        self.fiber1.start()

    def test_pr1557_receive_btc_uses_protocol_default_for_missing_final_expiry_delta(
        self,
    ):
        """TP-CCH-1557-001: a missing delta is a 24-hour safety margin, not zero."""
        parsed = self.fiber2.get_client().parse_invoice(
            {"invoice": MISSING_FINAL_EXPIRY_DELTA_INVOICE}
        )
        assert not any(
            "final_htlc_minimum_expiry_delta" in attr
            for attr in parsed["invoice"]["data"]["attrs"]
        )

        self._restart_cch_with_24_hour_btc_window()

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": MISSING_FINAL_EXPIRY_DELTA_INVOICE}
            )

        assert RECEIVE_BTC_FINAL_TLC_ERROR in str(exc_info.value)
