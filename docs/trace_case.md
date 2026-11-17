# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Case của nhóm: `seeded_regression` C-002@11

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run `E-seeded_regression-20261117-096d` (trace `R-seeded_regression-C-002-s11`) |
| Triệu chứng | Grader Lab 8 FAIL `status resolved != denied`. Ledger ghi 20.000đ (`refund_count` 1), nhãn là 0 và `denied`. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." khớp ledger, nên lỗi không nằm ở câu trả lời mà ở quyết định hoàn tiền. `db_fact_kept`, `refund_amount_correct` và `proposal_correct` cùng fail. |
| Bước sai đầu tiên | Event 3 `model_override {"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng hợp đồng (`check_applicable_policy`, `owner_order_only`). Event 2 `investigator` đọc đúng DB: O-202 `delay_days: 2`, `status: delivered`. Event 3 lấy số 5 từ ticket "Đơn giao trễ 5 ngày" đè lên số 2 của DB. Từ đó event 4 `quote` ra 20.000đ (`SHIP-LATE-03`) và host ghi tiền. |
| Failure mode | FM-03 ticket_trust |
| Đối chứng | `fixed C-002@11`: không có event `model_override`; event 3 `quote {"amount_vnd": 0, "reason": "no_entitlement"}`, status `denied`, ledger 0, Lab 8 PASS. Baseline `swarm` mắc đúng lỗi này ở C-002@55 (nhánh `seed % 5 == 0` của `swarm_reference`). |
| Regression test | `tests/test_lab9_trace_case.py`: `test_trace_case_c002_ticket_trust_caught_at_override_step` khẳng định `model_override` đứng trước `quote` và `db_fact_kept`, `refund_amount_correct`, `pass_rate` đều fail trên `seeded_regression`. `test_trace_case_c002_fixed_control_passes` pass trên `fixed`. Đã chạy: cả hai xanh trong `pytest -m student -k lab9`. Ở cấp gate, metric `db_fact_kept` và `refund_amount_correct` bắt được trial này. |
| Chưa chắc chắn | Trace chỉ cho thấy agent *chép* số từ ticket; MockLLM không cho biết một model thật sẽ tin ticket với tần suất nào. Trace cũng không cho thấy biến thể không để lại event (`quiet_override`): khi đó `db_fact_kept` mù, chỉ còn `refund_amount_correct` và slice `denial` bắt được. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
