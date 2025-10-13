from framework.basic_fiber import FiberTest


class TestShutdownChannel(FiberTest):
    """
        channel_id
        存在的channel
        不存在的channel
    close_script
    force
        在线的channel
        不在线的channel
    fee_rate
    qa
        channel状态
            创建还没成功的channel
            创建成功的channel
            交易中的channel
            关闭中的channel
            已经关闭的channel
        处于交易状态的channel 能否关闭
        对方节点状态
            不在线的channel
    """
