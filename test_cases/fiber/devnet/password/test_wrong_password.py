import pytest

from framework.basic_fiber import FiberTest


class TestFiber(FiberTest):

    def test_001(self):
        self.fiber1.stop()
        self.fiber1.start("password1")
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().node_info()
        expected_error_message = "request time out"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.fiber1.start("password0")
        self.fiber1.get_client().node_info()
