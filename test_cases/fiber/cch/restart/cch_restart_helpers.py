import hashlib
import time

from framework.basic_fiber_with_cch import FiberCchTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


def wait_lnd_invoice_state(lnd, payment_hash, expected, timeout=120):
    rhash = payment_hash[2:] if payment_hash.startswith("0x") else payment_hash
    last = None
    for _ in range(timeout):
        last = lnd.ln_cli_with_cmd(f"lookupinvoice {rhash}")
        if last["state"] == expected:
            return last
        time.sleep(1)
    raise TimeoutError(
        f"LND invoice {payment_hash} did not reach {expected}, last={last}"
    )


class CchRestartBase(FiberCchTest):
    def restart_cch(self, extra_config=None):
        config = {
            "cch": True,
            "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
            "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
        }
        if extra_config:
            config.update(extra_config)
        self.fiber1.stop()
        self.fiber1.prepare(update_config=config)
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)

    def open_wrapped_btc_channel_to_cch(self):
        self.fiber2.connect_peer(self.fiber1)
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )

    def new_wrapped_btc_fiber_invoice(self, amount_sats, **overrides):
        request = {
            "amount": hex(amount_sats),
            "currency": "Fibd",
            "description": "CCH restart receive_btc invoice",
            "udt_type_script": self.get_account_udt_script(self.fiber1.account_private),
            "payment_preimage": self.generate_random_preimage(),
            "hash_algorithm": "sha256",
        }
        request.update(overrides)
        return self.fiber2.get_client().new_invoice(request)
