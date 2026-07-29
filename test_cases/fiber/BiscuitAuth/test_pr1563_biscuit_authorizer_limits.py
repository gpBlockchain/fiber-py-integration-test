import pytest

from framework.basic_fiber import FiberTest
from framework.fiber_rpc import FiberRPCClient


BISCUIT_PUBLIC_KEY = (
    "ed25519/383faaf0aff783efe70479ff34d645ba0d3d729e541b55b17d6344c551bcb1cd"
)
TIMEOUT_PERMISSIONS_TOKEN = "EqYBCjwKBXBlZXJzGAMiCQoHCAASAxiACDImCiQKAggbEgYIBRICCAUaFgoECgIIBQoICgYggIvSuwYKBBoCCAISJAgAEiBJiOUmeIhW0F7iTkOokRAwA-DWGOA3LXpwleiPhO_7fBpAzueSyOOLnmBll1iGeX5GIjS3IiRLBQ4VTGqN2P9vc7B_Fkg-thsrBROWngupphmN5IalwhpDi2APL-6t97bmACIiCiD4zzBy5woFGESdj2iO2918lgjf2IM6Dal-JWe1VYr3ng=="


class TestPR1563BiscuitAuthorizerLimits(FiberTest):
    def test_expired_biscuit_token_keeps_generic_unauthorized_message(self):
        self.fiber1.stop()
        self.fiber1.start(rpc_biscuit_public_key=BISCUIT_PUBLIC_KEY)

        client = FiberRPCClient(
            self.fiber1.get_client().url,
            {"Authorization": f"Bearer {TIMEOUT_PERMISSIONS_TOKEN}"},
            1,
        )

        with pytest.raises(Exception) as exc_info:
            client.list_peers()

        assert "Error: Unauthorized" in exc_info.value.args[0]
        assert "timed out" not in exc_info.value.args[0]
