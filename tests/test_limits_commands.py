from orbitlink.commands import CmdState, Command, FifoQueue, Priority, PriorityRetryQueue
from orbitlink.limits import Limit, LimitChecker


def test_limit_persistence_and_recovery():
    lc = LimitChecker(limits={"v": Limit("v", red_low=6.6, yellow_low=7.0, persistence=2)})
    assert lc.check({"v": 6.9}) == []                 # first yellow sample: pending
    tr = lc.check({"v": 6.9})
    assert len(tr) == 1 and tr[0].new_state == "yellow" and tr[0].escalation
    assert lc.check({"v": 6.5}) == []
    assert lc.check({"v": 6.5})[0].new_state == "red"
    back = lc.check({"v": 7.5})                       # recovery is immediate
    assert back[0].new_state == "nominal" and not back[0].escalation


def test_limit_ignores_missing_and_disabled():
    lc = LimitChecker(limits={"t": Limit("t", red_high=60, enabled=False)})
    assert lc.check({"t": 99}) == []
    assert lc.check({"x": 1}) == []


def _cmd(i, prio, t=0.0, frames=1):
    return Command(i, f"c{i}", 1, b"", prio, t, frames=frames)


def test_fifo_order_and_resubmission_gets_new_seq():
    q = FifoQueue(ack_timeout_s=5)
    for i, p in enumerate([Priority.BULK, Priority.CRITICAL], start=1):
        q.submit(_cmd(i, p))
    first = q.next_to_send(0)
    assert first.id == 1                              # arrival order, priority ignored
    assert q.next_to_send(1) is None                  # stop-and-wait
    q.tick(10)                                        # timeout -> back of the queue
    old_seq = first.seq
    nxt = q.next_to_send(10)
    assert nxt.id == 2
    q.on_ack(nxt.seq, 0, 11)
    again = q.next_to_send(12)
    assert again.id == 1 and again.seq != old_seq and again.resubmissions == 1


def test_priority_retry_orders_by_class_and_retries_same_seq():
    q = PriorityRetryQueue(ack_timeout_s=5, max_retries=2, hold_s=100)
    q.submit(_cmd(1, Priority.BULK, 0))
    q.submit(_cmd(2, Priority.NORMAL, 1))
    q.submit(_cmd(3, Priority.CRITICAL, 2))
    c = q.next_to_send(3)
    assert c.id == 3
    seq = c.seq
    q.tick(9)
    c2 = q.next_to_send(9)
    assert c2.id == 3 and c2.seq == seq and c2.attempts == 2
    q.tick(15)
    q.next_to_send(15)
    q.tick(21)                                        # third timeout: burst budget used up
    nxt = q.next_to_send(21)
    assert nxt.id == 2                                # held command steps aside
    q.on_ack(nxt.seq, 0, 22)
    assert q.next_to_send(23).id == 1
    q.on_ack(q.in_flight.seq, 0, 24)
    assert q.next_to_send(50) is None                 # still held
    held = q.next_to_send(130)
    assert held.id == 3 and held.seq == seq


def test_priority_retry_aging_prevents_starvation():
    q = PriorityRetryQueue(aging_s=100)
    q.submit(_cmd(1, Priority.BULK, 0))
    q.submit(_cmd(2, Priority.NORMAL, 350))
    assert q.next_to_send(360).id == 1                # bulk waited 3.6 classes worth


def test_priority_retry_is_pass_aware():
    q = PriorityRetryQueue(ack_timeout_s=6)
    q.submit(_cmd(1, Priority.CRITICAL, 0, frames=40))
    q.submit(_cmd(2, Priority.NORMAL, 0, frames=1))
    c = q.next_to_send(0, remaining_s=30, frame_time_s=2.0)
    assert c.id == 2                                  # 40 frames x 2 s will not fit


def test_failed_after_attempt_cap_and_late_ack_ignored():
    q = PriorityRetryQueue(ack_timeout_s=1, max_retries=10, max_attempts=3)
    q.submit(_cmd(1, Priority.HIGH))
    for t in range(3):
        c = q.next_to_send(t * 2)
        q.tick(t * 2 + 1.5)
    assert c.state == CmdState.FAILED
    assert q.on_ack(c.seq, 0, 100) is None


def test_nack_is_terminal():
    q = FifoQueue()
    q.submit(_cmd(1, Priority.NORMAL))
    c = q.next_to_send(0)
    q.on_ack(c.seq, 2, 1)
    assert c.state == CmdState.NACKED and q.next_to_send(2) is None
