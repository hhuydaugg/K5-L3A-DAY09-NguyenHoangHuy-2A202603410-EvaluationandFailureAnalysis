# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

Judge chạy ở chế độ replay: verdict đã lưu sẵn trong CSV, 100 reply mỗi tiêu chí. FAIL là lớp dương (việc của judge là tìm lỗi).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | Đạt (tp 24, fp 4, fn 6, tn 66) | dùng, chỉ để theo dõi giọng văn, không chặn release |
| helpful | 100 | 0.375 | 1.000 | 0.300 | Không (tp 9, fp 0, fn 21, tn 70) | loại |

Tỉ lệ lỗi sau hiệu chỉnh Rogan-Gladen:
- **tone:** judge thấy 0.28, hiệu chỉnh ra 0.30.
- **helpful:** judge chỉ thấy 0.09, hiệu chỉnh ra 0.30. Nghĩa là judge helpful báo ít hơn khoảng 3 lần số reply thật sự không giúp được khách.

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | tone H-008 "Em đã hoàn tiền cho anh rồi nha" | PASS | FAIL | Người | Xưng "em" kèm "nha" là suồng sã với khách đang khiếu nại. Rubric: bắt buộc xưng "LogiX" và gọi khách "anh/chị"; xưng "em" hay gọi "bạn" là FAIL. |
| 2 | tone H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ… rồi nha" | PASS | FAIL | Người | Cùng lỗi xưng hô như #1; quy tắc của #1 bắt luôn ca này. |
| 3 | tone H-029 "Dạ, vui lòng chờ… không cần nhắn thêm" | PASS | FAIL | Người | Judge chỉ nhìn "Dạ, vui lòng" ở đầu câu. Rubric: chấm cả câu; câu cấm đoán khách ("không cần…") là FAIL. |
| 4 | tone H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu lời xin lỗi. Rubric: xin lỗi không bắt buộc khi yêu cầu đã được giải quyết; ngắn mà đủ ý là PASS. |
| 5 | tone H-051 "LogiX đã refund 90.000đ" | FAIL | PASS | Người | CS LogiX cho phép từ "refund". Rubric: thêm danh sách từ tiếng Anh nghiệp vụ được phép. |
| 6 | tone H-063 "đã hoàn 20.000đ và sẽ hoàn thêm" | PASS | FAIL | Người | Đây là lời hứa về tiền ngoài kết quả của hệ thống, không phải lỗi giọng. Tách khỏi tone; phần tiền chấm bằng code (`reply_matches_ledger`, FM-08). |
| 7 | tone H-070 "…cho đơn O-909 ạ 😂😂" | PASS | FAIL | Người | Rubric: không dùng emoji khi trả lời khiếu nại. |
| 8 | tone H-081 "đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | Judge | Người gán nhãn đọc nhầm ticket. Sửa nhãn người, không sửa rubric. |
| 9 | tone H-088 "Dạ, dạ, LogiX đã hoàn 40.000đ" | FAIL | PASS | Người | Lặp chữ do gõ nhầm là lỗi trình bày, không làm giọng văn mất lịch sự. Rubric ghi rõ: lỗi gõ không tính vào tone. |
| 10 | tone H-094 "Da, LogiX da hoan 40.000đ…" | PASS | FAIL | Người | Mất dấu tiếng Việt (dịch máy). Rubric: reply không dấu là FAIL. |
| 11 | helpful HL-016 (xin lỗi 2 lần + "đã hoàn 55.000đ", ledger 35.000đ) | PASS | FAIL | Người | Judge không thấy ledger nên không thể biết số tiền sai. Có 18/21 ca bỏ sót thuộc loại "sai tiền". |
| 12 | helpful HL-028 (lời xin lỗi lặp lại, "50.000đ", ledger 35.000đ) | PASS | FAIL | Người | Thiên vị độ dài: 13/13 reply dài hơn 150 ký tự được judge cho PASS, trong khi người cho FAIL cả 13. |
| 13 | helpful HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Không nói số tiền, không nói kết quả, xưng hô sai. Rubric helpful phải bắt buộc nêu kết quả và số tiền (nếu có). |

## Kết luận

**Loại `helpful`:**
- Recall 0.30: judge bỏ sót 21/30 reply mà người chấm FAIL. κ = 0.375 < 0.6.
- Precision 1.0 (fp = 0) chỉ có nghĩa là judge hiếm khi nói FAIL, không có nghĩa là judge tốt.
- 18/21 ca bỏ sót là reply nói sai số tiền. Đây là lỗi về tiền, phải chấm bằng code: grader `ReplyMatchesLedger` (metric `reply_matches_ledger`, chặn release) so mọi số tiền trong reply với ledger.
- Nếu dùng judge này làm gate, gate sẽ báo tỉ lệ lỗi 9% trong khi con số thật khoảng 30%.

**Dùng `tone_ok`:**
- Đạt cả 3 ngưỡng, nhưng sai lệch có hệ thống ở xưng hô (#1, #2) và ở việc đòi lời xin lỗi (#4).
- Chỉ dùng để theo dõi, không đưa vào metric chặn release cho tới khi sửa rubric theo các dòng trên và đo lại trên nhãn mới.

**Rủi ro còn lại:**
- Judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật.
- Một nhãn người sai (H-081) cho thấy cần 2 người chấm độc lập cho từng ca bất đồng.
