import time

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestIssue478(FiberTest):

    def test_issue_478(self):
        """
        https://github.com/nervosnetwork/fiber/pull/478
        Returns:

        """

        # start new fiber
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000),
            config=None,
            fiber_version=FiberConfigPath.CURRENT_TESTNET,
        )
        self.fiber4 = self.start_new_fiber(
            self.generate_account(10000),
            config=None,
            fiber_version=FiberConfigPath.CURRENT_DEV,
        )

        self.fiber3.connect_peer(self.fiber4)
        time.sleep(2)
        self.fiber3.get_client().graph_nodes({})
        self.fiber4.get_client().graph_nodes({})
