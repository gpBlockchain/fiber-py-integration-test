import time

import pytest

from framework.basic_fiber import FiberTest
from framework.fiber_rpc import FiberRPCClient

"""
biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535  read.channels.permissions.bc
EoEBChcKCGNoYW5uZWxzGAMiCQoHCAASAxiACBIkCAASINsP9Satqa_fFDCKxl97PRNMIwvOJyBQdYnf-y6soZV2GkC_bB7lUjFYkzPhFkuamoTtujZWCDieyC6MFHSqsA6GBOdHLClqrmfj8LLD6-px_Vu7J8rqFzcdiBa6SfvSJmkEIiIKILo1TpIlKgviVKd15vntWCSC68ZrabwnaGktOvkvLP7W%                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 read.node.permissions.bc
EnYKDBgDIggKBggAEgIYGBIkCAASINopyBP0DcRvsBVbVBICipz9TTYnUmsu2QltVG4ygSN1GkBfqunuzx6Hy3hOcondX--S0WFRBEpZ9rJ26R93_FuT3I8nyXjXnevNc4Mm_8XpcdYjYYmSXKsYZyr2XvBmC2YBIiIKINoaQJxg4vW2O077DEp-PFa3BTPlIwwqg6lZkFxduW0g%                                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 read.peers.permissions.bc
En4KFAoFcGVlcnMYAyIJCgcIABIDGIAIEiQIABIghUAHCKzo9Vcn7TREsZdGs-asA4hIOqu14qqX9jYynNEaQG48wMZ4XSPQShXa8eMILEtreIH9_scnhR8kG6Ob4HmubWw785txv0rUp59C2vq0EBrtRbJSV9elMT7wYwibgwAiIgogbG-4B7sVI8SkQb67go34PR-1uYEP69f8vBVxFyTnVAs=%                                                                                                                                                                               

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 with.time.permissions.bc
EqYBCjwKBXBlZXJzGAMiCQoHCAASAxiACDImCiQKAggbEgYIBRICCAUaFgoECgIIBQoICgYggMeUlQ8KBBoCCAISJAgAEiBhUyrODnAHKwW5mwHsM6ctigIB7Q-7qkGFYYqL-FoZAxpATv2rCmXewLYo_mpIxrlmQpYOK9aY3wHF7TzOA3CcrVdwAYmP1xmcQNVpg4qY8S3ivbKsoCiHHs0pZr0T6iW8DCIiCiDbZdEp2W9T48829r624rZJea29znATjjotAlqvHYWRQA==%                                                                                                                       

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.invoices.permissions.bc
EoEBChcKCGludm9pY2VzGAMiCQoHCAESAxiACBIkCAASIGP1E2XvPIjo7-ZWUcsxBbY9A41jMCHg1YXGn7gUpLJBGkDZnhe48CRog6UYrxTe1HXF0PQPQsSzkcRYrjzxIACRdWyIFn3htiVWquT_LETdb99zyPkOih53kS7_M-N2AJkEIiIKIJMbRnk0SEzafTZE145UXf47lpfGxaQ0zIMS4Qg0Z1uc%                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.peers.permissions.bc
En4KFAoFcGVlcnMYAyIJCgcIARIDGIAIEiQIABIgsCUDMwC8_udzem5EBgANEylv8BzEiuVCh_73dmkPETgaQIZJXHL5Wr0XtTo2-DCVubDfK8Am375EodtyDY_6avWowuxLkV8l5nWwmVLn1NWy6504t3-j8pUClwHL8C8row8iIgog46JbovXKs52uX1R_DE3nwmoWsEbVXeYuwrPbZpfpAjs=%                                                                                                                                                                               

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 read.graph.permissions.bc
En4KFAoFZ3JhcGgYAyIJCgcIABIDGIAIEiQIABIg_WPX5JWXGjmDXXr4kdX8WuNPOPp6yFTowiwci-FfoNsaQE2ceC8fXdXjZNAl84nnxIT23qxnZrt7eRPCRU7M4nWzF-jd6nQrht5-n5BARiP9bO7ojTZTGN_m7aVqvJvdxAsiIgogmmuIrTzzW96h9mGwiDLvYkdHf0QOVwySjSslgRX7j1Q=                                                                                                                                                                               

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 read.payments.permissions.bc
EoEBChcKCHBheW1lbnRzGAMiCQoHCAASAxiACBIkCAASIGYsRFFn589TOs6VHuZ6-BSGLTuJCsKVaJQlZGWgqtviGkCe21ZH7UsOAmlxmfffNWq7z7Jwl5EruNrGs1SWew8UJesZQhDsKh2Ady7gDtMoND4gEODraec1T76bvLHcGl8KIiIKIClDc2CoQYflPtisdyVLUUS6e1CWaharIj_DCwzwK86M%                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 rpc.permissions.bc
EsQCCtkBCghjaGFubmVscwoIbWVzc2FnZXMKBWNoYWluCgVncmFwaAoIaW52b2ljZXMKCHBheW1lbnRzCgVwZWVycwoKd2F0Y2h0b3dlchgDIgkKBwgBEgMYgAgiCQoHCAASAxiACCIJCgcIARIDGIEIIgkKBwgBEgMYgggiCQoHCAASAxiDCCIICgYIABICGBgiCQoHCAESAxiECCIJCgcIABIDGIQIIgkKBwgBEgMYhQgiCQoHCAASAxiFCCIJCgcIARIDGIYIIgkKBwgAEgMYhggiCQoHCAESAxiHCBIkCAASILebWZiVMVNRSxMq-CED9Y0GCKe7VMoYKjBhWKmJTg8sGkCyUmmbk7SA-fsbwQdDvA3hAhC86lT3KmoyP5hQvaTNS2PrzTrcoV4u2M1LqCBtoVFqrE7kMB9D5nfo_A2S6fYAIiIKIEw4XnRlMyolWgVi_b56CCU7y4_vNhF4CP8uolIx58TV%                                                                                                             

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.chain.permissions.bc
En4KFAoFY2hhaW4YAyIJCgcIARIDGIAIEiQIABIgK8pWKnqb6Tc-1yxY7KTwku2pUSybK57p66xYxVH0v34aQIJn8JuYs8bMTJU2jScLc_eRb4RURq9fKjHc8oqHiPvV4AG6k7_Eu_w8MeOZhBDALSEQd9RYxNNY8-MMRBqGRwsiIgogFizTMLygvkIL3k_pOYjqjY9YKOMYWvVioXQAArqO8ds=%                                                                                                                                                                               

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.message.permissions.bc
EoEBChcKCG1lc3NhZ2VzGAMiCQoHCAESAxiACBIkCAASIH_QlrvivLt1ON8ALvSv_LW0wAC9R11whTQ88QjXblxdGkBRMvAjmzsPeQK8RT3wLC3-GjjidHi_Poh4gEEN4DvEfUBZ3dgUXE8lVRlPJsZPWLP-45HLcwXlHtv1iA1X12QEIiIKIKYquYDxsVe2TF3XS98HagDBzh4znzpm9bV700bizzbc%                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.watchtower.permissions.bc 
EoMBChkKCndhdGNodG93ZXIYAyIJCgcIARIDGIAIEiQIABIgO3N9hDIGRelKRiR_cmFvTJLbZxzJPKATQkiDV4sot8waQNenFDsHMkjqjeLRmZToXBLkmWN5AH6GuqMkJrOlO_Z1CqjGNRWaDer7bJiBnvgM5hXKJxa6iCSNAwYq37J8oAkiIgog_QaTJZbuJRTzwZMK9ebWqPBI6tF9LfEKaI32bRUK3o8=%                                                                                                                                                                       

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 read.invoices.permissions.bc   
EoEBChcKCGludm9pY2VzGAMiCQoHCAASAxiACBIkCAASICVntbqaMdAoL0RnPlYa9hZIKSHteY35WPq7rZeAqIiOGkBdpL3n1zj8vLXOgx_aj6y8x9QrNpXWhzEn3ZlG8CpfEl-5g9gr8FQkHR0vu2q2Db08TNeuC2_F6v3PS9n2qUEIIiIKIOn-geVslpAMBl3-9FjGnM66LS-FVDKAy1j80KMDQ3Yw%                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 read.peer.permissions.bc    
En4KFAoFcGVlcnMYAyIJCgcIABIDGIAIEiQIABIg83zznPCI7oR4mAPaaARcdmqeSCsj7tvk6rn8J-qQEW4aQKfXZZmBO5q2w7oyN1mWUrrJdn4ZVXhEsCFXmRiOytJAv0JXK9_SQEI50cCo3duUB0hCKsZGzHScyB5f6HziWQMiIgogwyXOIe4t6U6gZV9eDfeAP6JUK_AP_6wnnVXg3IiP93g=%                                                                                                                                                                               

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 timeout.permissions.bc  
EqYBCjwKBXBlZXJzGAMiCQoHCAASAxiACDImCiQKAggbEgYIBRICCAUaFgoECgIIBQoICgYggIvSuwYKBBoCCAISJAgAEiBJiOUmeIhW0F7iTkOokRAwA-DWGOA3LXpwleiPhO_7fBpAzueSyOOLnmBll1iGeX5GIjS3IiRLBQ4VTGqN2P9vc7B_Fkg-thsrBROWngupphmN5IalwhpDi2APL-6t97bmACIiCiD4zzBy5woFGESdj2iO2918lgjf2IM6Dal-JWe1VYr3ng==%                                                                                                                       

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.channels.permissions.bc
EoEBChcKCGNoYW5uZWxzGAMiCQoHCAESAxiACBIkCAASIAniDe3GeZnq6wHsXnazEucFmdbTFGRcHXfW8AoBwh_XGkAEqzz0DA7rzh3VIiimIMXK4Z-CaGCECT2eNel9z8MZ116ylc8yHMxD8vHFFiOeA2l92A1gQds43dyz11dZa0QBIiIKIBYcyHeA6CxHsdRsX3kbOlcAFirp_1gUSPJVvsB5ofXL%                                                                                                                                                                           

guopenglin@MacBook-Pro-4 permissions % biscuit generate --private-key ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535 write.payments.permissions.bc
EoEBChcKCHBheW1lbnRzGAMiCQoHCAESAxiACBIkCAASIM9orpucr8t8L1Qlv28OejTiNo1ws9vlxwzdk9gZEhjVGkDGd4A3W5WmHJB9IRiV3UROgPE4eL_F2UzOuJdHOiRPwjdxsdSOQYB00oWeHW70K7knKCEC8pG2Z4dIklgHFTAAIiIKILKXG0X7AtjjwXkBYdcBlbRtXwhiezIvMkkFWvFrbpSh%
"""


class TestAuth(FiberTest):

    def test_demo(self):
        self.fiber1.stop()
        self.fiber1.start(
            rpc_biscuit_public_key="ed25519-private/f02ac69460ca158e88d5728cb8ae26aab9aa172f3db4e95ed6e9f729e2212535"
        )
        # # todo asser start failed
        # run_command("lsof -i:8228", False)
        # # 符合规范的id :ed25519/xxxx
        time.sleep(1)
        self.fiber1.start(
            rpc_biscuit_public_key="ed25519/383faaf0aff783efe70479ff34d645ba0d3d729e541b55b17d6344c551bcb1cd"
        )
        time.sleep(3)
        # read.channels.permissions.bc
        readChannelsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCGNoYW5uZWxzGAMiCQoHCAASAxiACBIkCAASINsP9Satqa_fFDCKxl97PRNMIwvOJyBQdYnf-y6soZV2GkC_bB7lUjFYkzPhFkuamoTtujZWCDieyC6MFHSqsA6GBOdHLClqrmfj8LLD6-px_Vu7J8rqFzcdiBa6SfvSJmkEIiIKILo1TpIlKgviVKd15vntWCSC68ZrabwnaGktOvkvLP7W"
            },
            1,
        )
        # read.graph.permissions.bc
        readGraphFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer En4KFAoFZ3JhcGgYAyIJCgcIABIDGIAIEiQIABIg_WPX5JWXGjmDXXr4kdX8WuNPOPp6yFTowiwci-FfoNsaQE2ceC8fXdXjZNAl84nnxIT23qxnZrt7eRPCRU7M4nWzF-jd6nQrht5-n5BARiP9bO7ojTZTGN_m7aVqvJvdxAsiIgogmmuIrTzzW96h9mGwiDLvYkdHf0QOVwySjSslgRX7j1Q="
            },
            1,
        )
        # read.invoices.permissions.bc
        readInvoicesFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCGludm9pY2VzGAMiCQoHCAASAxiACBIkCAASICVntbqaMdAoL0RnPlYa9hZIKSHteY35WPq7rZeAqIiOGkBdpL3n1zj8vLXOgx_aj6y8x9QrNpXWhzEn3ZlG8CpfEl-5g9gr8FQkHR0vu2q2Db08TNeuC2_F6v3PS9n2qUEIIiIKIOn-geVslpAMBl3-9FjGnM66LS-FVDKAy1j80KMDQ3Yw"
            },
            1,
        )
        # read.node.permissions.bc
        readNodeFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EnYKDBgDIggKBggAEgIYGBIkCAASINopyBP0DcRvsBVbVBICipz9TTYnUmsu2QltVG4ygSN1GkBfqunuzx6Hy3hOcondX--S0WFRBEpZ9rJ26R93_FuT3I8nyXjXnevNc4Mm_8XpcdYjYYmSXKsYZyr2XvBmC2YBIiIKINoaQJxg4vW2O077DEp-PFa3BTPlIwwqg6lZkFxduW0g"
            },
            1,
        )
        # read.payments.permissions.bc
        readPaymentsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCHBheW1lbnRzGAMiCQoHCAASAxiACBIkCAASIGYsRFFn589TOs6VHuZ6-BSGLTuJCsKVaJQlZGWgqtviGkCe21ZH7UsOAmlxmfffNWq7z7Jwl5EruNrGs1SWew8UJesZQhDsKh2Ady7gDtMoND4gEODraec1T76bvLHcGl8KIiIKIClDc2CoQYflPtisdyVLUUS6e1CWaharIj_DCwzwK86M"
            },
            1,
        )
        # read.peer.permissions.bc
        readPeersPermissionsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer En4KFAoFcGVlcnMYAyIJCgcIABIDGIAIEiQIABIg83zznPCI7oR4mAPaaARcdmqeSCsj7tvk6rn8J-qQEW4aQKfXZZmBO5q2w7oyN1mWUrrJdn4ZVXhEsCFXmRiOytJAv0JXK9_SQEI50cCo3duUB0hCKsZGzHScyB5f6HziWQMiIgogwyXOIe4t6U6gZV9eDfeAP6JUK_AP_6wnnVXg3IiP93g="
            },
            1,
        )
        # read.peers.permissions.bc
        # rpc.permissions.bc
        rpcPermissionsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EsQCCtkBCghjaGFubmVscwoIbWVzc2FnZXMKBWNoYWluCgVncmFwaAoIaW52b2ljZXMKCHBheW1lbnRzCgVwZWVycwoKd2F0Y2h0b3dlchgDIgkKBwgBEgMYgAgiCQoHCAASAxiACCIJCgcIARIDGIEIIgkKBwgBEgMYgggiCQoHCAASAxiDCCIICgYIABICGBgiCQoHCAESAxiECCIJCgcIABIDGIQIIgkKBwgBEgMYhQgiCQoHCAASAxiFCCIJCgcIARIDGIYIIgkKBwgAEgMYhggiCQoHCAESAxiHCBIkCAASILebWZiVMVNRSxMq-CED9Y0GCKe7VMoYKjBhWKmJTg8sGkCyUmmbk7SA-fsbwQdDvA3hAhC86lT3KmoyP5hQvaTNS2PrzTrcoV4u2M1LqCBtoVFqrE7kMB9D5nfo_A2S6fYAIiIKIEw4XnRlMyolWgVi_b56CCU7y4_vNhF4CP8uolIx58TV"
            },
            1,
        )
        # timeout.permissions.bc
        timeoutPermissionsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EqYBCjwKBXBlZXJzGAMiCQoHCAASAxiACDImCiQKAggbEgYIBRICCAUaFgoECgIIBQoICgYggIvSuwYKBBoCCAISJAgAEiBJiOUmeIhW0F7iTkOokRAwA-DWGOA3LXpwleiPhO_7fBpAzueSyOOLnmBll1iGeX5GIjS3IiRLBQ4VTGqN2P9vc7B_Fkg-thsrBROWngupphmN5IalwhpDi2APL-6t97bmACIiCiD4zzBy5woFGESdj2iO2918lgjf2IM6Dal-JWe1VYr3ng=="
            },
            1,
        )
        # with.time.permissions.bc
        withTimePermissionsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EqYBCjwKBXBlZXJzGAMiCQoHCAASAxiACDImCiQKAggbEgYIBRICCAUaFgoECgIIBQoICgYggMeUlQ8KBBoCCAISJAgAEiBhUyrODnAHKwW5mwHsM6ctigIB7Q-7qkGFYYqL-FoZAxpATv2rCmXewLYo_mpIxrlmQpYOK9aY3wHF7TzOA3CcrVdwAYmP1xmcQNVpg4qY8S3ivbKsoCiHHs0pZr0T6iW8DCIiCiDbZdEp2W9T48829r624rZJea29znATjjotAlqvHYWRQA=="
            },
            1,
        )
        # write.chain.permissions.bc
        writeChainPermissionsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer En4KFAoFY2hhaW4YAyIJCgcIARIDGIAIEiQIABIgK8pWKnqb6Tc-1yxY7KTwku2pUSybK57p66xYxVH0v34aQIJn8JuYs8bMTJU2jScLc_eRb4RURq9fKjHc8oqHiPvV4AG6k7_Eu_w8MeOZhBDALSEQd9RYxNNY8-MMRBqGRwsiIgogFizTMLygvkIL3k_pOYjqjY9YKOMYWvVioXQAArqO8ds="
            },
            1,
        )
        # write.channels.permissions.bc
        writeChannelsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCGNoYW5uZWxzGAMiCQoHCAESAxiACBIkCAASIAniDe3GeZnq6wHsXnazEucFmdbTFGRcHXfW8AoBwh_XGkAEqzz0DA7rzh3VIiimIMXK4Z-CaGCECT2eNel9z8MZ116ylc8yHMxD8vHFFiOeA2l92A1gQds43dyz11dZa0QBIiIKIBYcyHeA6CxHsdRsX3kbOlcAFirp_1gUSPJVvsB5ofXL"
            },
            1,
        )
        # write.invoices.permissions.bc
        writeInvoicesFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCGludm9pY2VzGAMiCQoHCAESAxiACBIkCAASIGP1E2XvPIjo7-ZWUcsxBbY9A41jMCHg1YXGn7gUpLJBGkDZnhe48CRog6UYrxTe1HXF0PQPQsSzkcRYrjzxIACRdWyIFn3htiVWquT_LETdb99zyPkOih53kS7_M-N2AJkEIiIKIJMbRnk0SEzafTZE145UXf47lpfGxaQ0zIMS4Qg0Z1uc"
            },
            1,
        )
        # write.message.permissions.bc
        writeMessageFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCG1lc3NhZ2VzGAMiCQoHCAESAxiACBIkCAASIH_QlrvivLt1ON8ALvSv_LW0wAC9R11whTQ88QjXblxdGkBRMvAjmzsPeQK8RT3wLC3-GjjidHi_Poh4gEEN4DvEfUBZ3dgUXE8lVRlPJsZPWLP-45HLcwXlHtv1iA1X12QEIiIKIKYquYDxsVe2TF3XS98HagDBzh4znzpm9bV700bizzbc"
            },
            1,
        )
        # write.payments.permissions.bc
        writePaymentsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoEBChcKCHBheW1lbnRzGAMiCQoHCAESAxiACBIkCAASIM9orpucr8t8L1Qlv28OejTiNo1ws9vlxwzdk9gZEhjVGkDGd4A3W5WmHJB9IRiV3UROgPE4eL_F2UzOuJdHOiRPwjdxsdSOQYB00oWeHW70K7knKCEC8pG2Z4dIklgHFTAAIiIKILKXG0X7AtjjwXkBYdcBlbRtXwhiezIvMkkFWvFrbpSh"
            },
            1,
        )
        # write.peers.permissions.bc
        writePeersPermissionsFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer En4KFAoFcGVlcnMYAyIJCgcIARIDGIAIEiQIABIgsCUDMwC8_udzem5EBgANEylv8BzEiuVCh_73dmkPETgaQIZJXHL5Wr0XtTo2-DCVubDfK8Am375EodtyDY_6avWowuxLkV8l5nWwmVLn1NWy6504t3-j8pUClwHL8C8row8iIgog46JbovXKs52uX1R_DE3nwmoWsEbVXeYuwrPbZpfpAjs="
            },
            1,
        )
        # write.watchtower.permissions.bc
        writeWatchtowerFiberClient = FiberRPCClient(
            self.fiber1.get_client().url,
            {
                "Authorization": "Bearer EoMBChkKCndhdGNodG93ZXIYAyIJCgcIARIDGIAIEiQIABIgO3N9hDIGRelKRiR_cmFvTJLbZxzJPKATQkiDV4sot8waQNenFDsHMkjqjeLRmZToXBLkmWN5AH6GuqMkJrOlO_Z1CqjGNRWaDer7bJiBnvgM5hXKJxa6iCSNAwYq37J8oAkiIgog_QaTJZbuJRTzwZMK9ebWqPBI6tF9LfEKaI32bRUK3o8="
            },
            1,
        )

        # write("channels")
        with pytest.raises(Exception) as exc_info:
            writeChannelsFiberClient.open_channel({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            rpcPermissionsFiberClient.open_channel({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            writeChannelsFiberClient.accept_channel({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            writeChannelsFiberClient.abandon_channel({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        with pytest.raises(Exception) as exc_info:
            writeChannelsFiberClient.shutdown_channel({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            writeChannelsFiberClient.update_channel({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # todo add_tlc
        # todo remove_tlc

        with pytest.raises(Exception) as exc_info:
            writeChannelsFiberClient.list_channels({})
        expected_error_message = "Unauthorized"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # read("channels");
        list_channels = readChannelsFiberClient.list_channels({})
        assert list_channels["channels"] == []
        rpc_list_channels = rpcPermissionsFiberClient.list_channels({})
        assert rpc_list_channels["channels"] == []

        with pytest.raises(Exception) as exc_info:
            readChannelsFiberClient.open_channel({})
        expected_error_message = "Unauthorized"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # todo write("messages")
        #     commitment_signed
        # todo  write("chain")
        #     submit_commitment_transaction
        # read("graph");

        #     graph_nodes
        nodes = readGraphFiberClient.graph_nodes({})
        assert len(nodes["nodes"]) == 2
        nodes = rpcPermissionsFiberClient.graph_nodes({})
        assert len(nodes["nodes"]) == 2

        #     graph_channels
        channels = readGraphFiberClient.graph_channels({})
        assert channels["channels"] == []

        # read("node");
        #     node_info
        node_info = readNodeFiberClient.node_info()
        assert node_info["tlc_min_value"] == "0x0"
        # write("invoices");
        #     new_invoice
        invoice = writeInvoicesFiberClient.new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        #     cancel_invoice
        writeInvoicesFiberClient.cancel_invoice(
            {"payment_hash": invoice["invoice"]["data"]["payment_hash"]}
        )
        # read("invoices");
        #     parse_invoice
        readInvoicesFiberClient.parse_invoice({"invoice": invoice["invoice_address"]})
        #     get_invoice
        invoice = readInvoicesFiberClient.get_invoice(
            {"payment_hash": invoice["invoice"]["data"]["payment_hash"]}
        )
        assert invoice["status"] == "Cancelled"
        # write("payments")
        #     send_payment
        with pytest.raises(Exception) as exc_info:
            writePaymentsFiberClient.send_payment({})
        expected_error_message = "Failed to validate payment request"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        #     send_payment_with_router
        with pytest.raises(Exception) as exc_info:
            writePaymentsFiberClient.send_payment_with_router({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # read("payments");
        #     get_payment
        with pytest.raises(Exception) as exc_info:
            readPaymentsFiberClient.get_payment({})
        expected_error_message = "Invalid param"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # write("peers");
        #     connect_peer
        writePeersPermissionsFiberClient.connect_peer(
            {"address": self.fiber2.get_client().node_info()["addresses"][0]}
        )
        #     disconnect_peer
        writePeersPermissionsFiberClient.disconnect_peer(
            {"peer_id": self.fiber2.get_peer_id()}
        )
        # read("peers")
        #     list_peers
        peers = readPeersPermissionsFiberClient.list_peers()
        assert peers["peers"] == []
        # todo write("watchtower");
        #     create_watch_channel
        #     remove_watch_channel
        #     update_revocation
        #     update_local_settlement
        #     create_preimage
        #     remove_preimage
        # todo right({channel_id}, "watchtower")
        #     create_watch_channel

        # - 限时
        #     - $time 限制token有效期
        with pytest.raises(Exception) as exc_info:
            timeoutPermissionsFiberClient.list_peers()
        expected_error_message = "Unauthorized"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        peers = withTimePermissionsFiberClient.list_peers()
        readPeers = readPeersPermissionsFiberClient.list_peers()
        assert peers["peers"] == readPeers["peers"]
