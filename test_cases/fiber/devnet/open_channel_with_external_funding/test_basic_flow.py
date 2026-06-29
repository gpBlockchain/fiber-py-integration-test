from test_cases.fiber.devnet.open_channel_with_external_funding.external_funding_base import (
    ExternalFundingBase,
)


class TestExternalFundingBasicFlow(ExternalFundingBase):
    """
    PR-1120 basic integration coverage.

    Mapped doc cases:
    - T-01 open external funding channel and inspect unsigned tx
    - T-02 submit signed funding tx and observe state progression
    - T-18 public channel support
    - T-19 custom funding_lock_script_cell_deps propagation
    - T-21 pre-submit list_channels observation

    Note:
    - T-04 is intentionally consolidated into test_main_flow.py to avoid
      running a second full ready-handshake verification path.
    """

    __test__ = True

    def test_open_channel_returns_unsigned_funding_tx(self):
        """
        T-01: verify open_channel_with_external_funding returns channel_id and
        an unsigned funding transaction with placeholder witnesses.
        """
        context = self._open_external_funding_channel(public=True)
        unsigned_funding_tx = context["unsigned_funding_tx"]

        assert context["channel_id"].startswith("0x")
        assert len(context["channel_id"]) == 66
        assert len(unsigned_funding_tx["outputs"]) > 0
        # External funding unsigned tx uses zero-filled placeholder witnesses rather than
        # real signatures. The dynamic witness payload should therefore remain all zeros.
        assert all(
            witness.startswith("0x") and int(witness[42:], 16) == 0
            for witness in unsigned_funding_tx["witnesses"]
        )

    def test_submit_signed_funding_tx_progresses_channel_state(self):
        """
        T-02: verify submit_signed_funding_tx accepts a properly signed tx and
        moves the initiator out of AwaitingExternalFunding.
        """
        context = self._open_external_funding_channel(public=True)
        signed_funding_tx = self._sign_external_funding_tx(
            context["unsigned_funding_tx"], context["external_private_key"]
        )

        submit_result = self._submit_external_funding(
            context["channel_id"], signed_funding_tx
        )

        assert submit_result["channel_id"] == context["channel_id"]
        assert submit_result["funding_tx_hash"].startswith("0x")

        progressed_channel = self._wait_until_state_not(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            context["channel_id"],
            "AwaitingExternalFunding",
            timeout=20,
        )
        assert progressed_channel["state"]["state_name"] != "AwaitingExternalFunding"

    def test_external_funding_public_channel(self):
        """T-18: verify external funding channels can be opened as public."""
        context = self._open_sign_submit_external_channel(public=True)

        self.Miner.miner_until_tx_committed(self.node, context["funding_tx_hash"], True)
        self._wait_both_channel_ready(context["channel_id"])

        initiator_channel = self._get_channel_by_id(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), context["channel_id"]
        )
        acceptor_channel = self._get_channel_by_id(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), context["channel_id"]
        )
        assert initiator_channel["is_public"] is True
        assert acceptor_channel["is_public"] is True

    def test_unsigned_funding_tx_contains_custom_cell_deps(self):
        """
        T-19: verify user-supplied funding_lock_script_cell_deps are copied into
        unsigned_funding_tx.cell_deps.
        """
        deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()
        custom_dep = {
            "out_point": {
                "tx_hash": deploy_hash,
                "index": hex(deploy_index),
            },
            "dep_type": "code",
        }
        context = self._open_external_funding_channel(
            public=True,
            extra_params={"funding_lock_script_cell_deps": [custom_dep]},
        )

        assert custom_dep in context["unsigned_funding_tx"]["cell_deps"]

    def test_list_channels_before_submit_shows_absent_or_awaiting_external_funding(
        self,
    ):
        """
        T-21: before submit, the channel may be absent from list_channels or it
        may already appear in the "awaiting external funding" phase.

        The PR-1252 state-machine refactor folded the standalone
        `AwaitingExternalFunding` state into
        `NegotiatingFunding(AWAITING_EXTERNAL_FUNDING)`. So the valid
        observable states are now:
          * state_name == "NegotiatingFunding" with state_flags containing
            "AWAITING_EXTERNAL_FUNDING", OR
          * legacy "AwaitingExternalFunding" (for older binaries).
        """
        context = self._open_external_funding_channel(public=True)
        initiator_channel = self._find_channel_by_id(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), context["channel_id"]
        )
        acceptor_channel = self._find_channel_by_id(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), context["channel_id"]
        )

        for channel in (initiator_channel, acceptor_channel):
            if channel is None:
                continue
            state = channel["state"]
            name = state["state_name"]
            flags = state.get("state_flags", "")
            ok = name == "AwaitingExternalFunding" or (
                name == "NegotiatingFunding"
                and "AWAITING_EXTERNAL_FUNDING" in (flags or "")
            )
            assert ok, (
                f"pre-submit channel should be awaiting external funding, "
                f"got state_name={name!r}, state_flags={flags!r}"
            )
