import os
import shutil

import pytest
import yaml

from framework.basic_fiber import FiberTest
from framework.test_fiber import Fiber


# Preserve 0x-prefixed CKB script fields when rewriting the generated config.
class HexStringLoader(yaml.SafeLoader):
    pass


def preserve_hex_strings(loader, node):
    if node.value.startswith(("0x", "0X")):
        return node.value
    return yaml.SafeLoader.construct_yaml_int(loader, node)


HexStringLoader.add_constructor("tag:yaml.org,2002:int", preserve_hex_strings)


class TestPR1698InvoiceNetwork(FiberTest):
    def test_signed_foreign_network_invoice_is_rejected(self):
        amount = 100000000
        self.open_channel(self.fiber1, self.fiber2, 200 * amount, 100 * amount)
        payer = self.fiber1.get_client()
        payee = self.fiber2.get_client()

        signer = Fiber.init_by_port(
            self.fiber_version,
            self.account2_private_key,
            "fiber/pr1698-signer",
            "8251",
            "8302",
        )
        self.fibers.append(signer)
        signer.prepare({"ckb_rpc_url": self.node.rpcUrl})
        # Keep the payee identity while signing an invoice for another network.
        os.makedirs(f"{signer.tmp_path}/fiber", exist_ok=True)
        shutil.copyfile(
            f"{self.fiber2.tmp_path}/fiber/sk", f"{signer.tmp_path}/fiber/sk"
        )
        with open(signer.fiber_config_path) as config_file:
            config = yaml.load(config_file, Loader=HexStringLoader)
        config["fiber"]["chain"] = "testnet"
        with open(signer.fiber_config_path, "w") as config_file:
            yaml.safe_dump(config, config_file)
        signer.start()
        assert signer.get_pubkey() == self.fiber2.get_pubkey()

        route = payer.build_router(
            {
                "amount": hex(amount),
                "hops_info": [{"pubkey": self.fiber2.get_pubkey()}],
            }
        )["router_hops"]
        balance_before = payer.list_channels({"pubkey": self.fiber2.get_pubkey()})[
            "channels"
        ][0]

        for method, with_router in (
            (payer.send_payment, False),
            (payer.send_payment_with_router, True),
        ):
            for dry_run in (False, True):
                foreign_invoice = signer.get_client().new_invoice(
                    {
                        "amount": hex(amount),
                        "currency": "Fibt",
                        "description": "PR 1698 foreign network invoice",
                        "expiry": "0xe10",
                        "payment_preimage": self.generate_random_preimage(),
                        "hash_algorithm": "sha256",
                    }
                )
                parsed = payer.parse_invoice(
                    {"invoice": foreign_invoice["invoice_address"]}
                )["invoice"]
                assert parsed["currency"] == "Fibt"
                assert any(
                    attr.get("payee_public_key") == self.fiber2.get_pubkey()
                    for attr in parsed["data"]["attrs"]
                )
                payment_hash = parsed["data"]["payment_hash"]

                params = {
                    "invoice": foreign_invoice["invoice_address"],
                    "dry_run": dry_run,
                }
                if with_router:
                    params["router"] = route
                with pytest.raises(
                    Exception,
                    match="invoice currency fibt does not match node currency fibd",
                ):
                    method(params)
                with pytest.raises(Exception, match="Payment session not found"):
                    payer.get_payment({"payment_hash": payment_hash})
                channel = payer.list_channels({"pubkey": self.fiber2.get_pubkey()})[
                    "channels"
                ][0]
                assert channel["local_balance"] == balance_before["local_balance"]
                assert channel["remote_balance"] == balance_before["remote_balance"]
                assert channel["offered_tlc_balance"] == "0x0"

        local_invoice = payee.new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": "PR 1698 matching network invoice",
                "expiry": "0xe10",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        payment = payer.send_payment({"invoice": local_invoice["invoice_address"]})
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
