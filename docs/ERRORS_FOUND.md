# Errors found, and how the design surfaced them

The packet "contains confident mistakes". Every item below is produced by a check in `app/checks.py` that recomputes from source evidence. None of them is hard-coded as a conclusion, and the same checks would run on another packet. Each one appears in **Findings & checks** with its evidence (clickable down to the highlighted field on the page), a proposed adjustment, and an estimated tax effect.

Effects are estimated with the packet's `tax_effect_rates` (v3), as the README instructs, because the engine can't be re-run. **+** means more tax or a higher balance due.

## A. Engine errors (the engine had the right evidence and used it wrongly)

| # | Error | Evidence | How it surfaced | Fix | Est. effect (fed / CA) |
|---|---|---|---|---|---|
| 1 | **RSU sales taxed twice.** Three noncovered lots carried $0 basis; the vest value was already W-2 wages | D28 p3, D11 p2 (adj. basis 19,100 / 19,100 / 9,420), W-2 box 14 | Each $0-basis 1099-B lot matched to the plan statement by acquisition date | Gains 53,020 → 5,400 | **−15,536 / −1,619** |
| 2 | **1099-K counted twice.** Engine added Paywell 12,400 to books revenue | D21, D15 p3 ("12,400 of Atlas' 41,200 came via Paywell, deposited gross"), D16-D20 | Three-way check: books 151,300 = 1099-NECs 142,000 + non-1099 9,300 exactly; engine 163,700 | Gross receipts 151,300 | −4,402 / −384 |
| 3 | **Mileage page scanned twice.** Engine summed every page total | D23 p2 = p3 ("same coffee stain") | Pages compared for duplicate content before summing; odometer cross-check | 11,800 → 8,100 mi; car expense 8,260 → 5,670 | +919 / +80 |
| 4 | **SE health insurance deducted** although Erik was eligible for Ngozi's employer plan | qa-25, D25; 2024 preparer reached the same conclusion | Questionnaire answers tested against each deduction's eligibility | Sch 1 line 17 → 0 | +4,464 / +605 |
| 5 | **Northlake interest counted twice** (composite 1099 and the standalone 1099-INT for the same sweep account) | D28 p1, D29 ("account ending 7731") | Equal payer amounts whose documents name the same account | Remove 1,840 | −640 / −127 |
| 6 | **Swedish interest converted at the 2024 rate** (10.56) instead of the 2025 average (9.86) | D36, `ref:fx.SEK.2024_average` | Every FX source must match the income's tax year | 1,747 → 1,871 | +43 / +9 |
| 7 | **California payment designated to 2024 counted as a 2025 estimate** | D68 (pay5 year = 2024, read correctly), FTB account | Payments agreed to the agency record by designated year, not by date | 14,000 → 10,500 | CA balance **+3,500** |
| 8 | **California-source wages include Texas work.** Halvorsen kept CA payroll until 09/30 | W-2 box 16 111,300 vs move-date paystub 22,469 (D73), qa-45 | W-2 state wages tested against residency dates | Col. E wages −88,831 | CA **−5,685** |
| 9 | **Payoff interest counted twice** in mortgage interest | D55 ("includes interest paid at payoff"), D56 | "Included in" language read before adding documents | −2,150 | +559 / +103 |
| 10 | **Seller's share of home property tax deducted.** Seller credited 01/01-07/07 at closing | D54 (credit 6,912), D58 ("owner of record 01/01: prior owner") | Closing-statement prorations applied in the year of purchase | 13,420 → 6,508; SALT drops below the cap | +1,462 / +332 |
| 11 | **Care provider name wrong** on Form 2441 ("Bright Oak Preschool") | D61, 2024 return | Provider identity agreed to source and prior year | Use "Bright Oak Kids Club" | - |

## B. Extraction errors (the pipeline read the page wrong)

| # | Error | How it surfaced |
|---|---|---|
| 12 | **Provider TIN misread at 0.97 confidence:** extracted 74-3829**51**6, printed 74-3829**15**6 (D62). Flows to Form 2441 | *Re-read check:* every extracted identifier must appear verbatim on its page. The document view shows "1 extracted value not found in the printed text" |
| 13 | **Meridian page 32 never read:** lot M18 (proceeds 250, basis 310, short-term noncovered) is missing. The engine flagged the 250 summary gap but never resolved it | Partially extracted documents have their unread pages checked against the summary. The page is marked "not read by the pipeline" in the document view. Effect −21 / −2 |
| 14 | **Church receipt "$?,500"** read as 1,500 at confidence 0.41 | Low-confidence reads go to a person. Kept 1,500 (client confirmed, check #2207) as a recorded judgment, with a written acknowledgment requested (over 250 needs one) |

## C. Client data that the documents contradict

| # | Item | Resolution |
|---|---|---|
| 15 | **Theo's SSN transposed** in the organizer (…3309 vs …3390 on the 2024 return and SS card). This caused the 07/13 e-file rejection | Identity fields agreed to the prior-year return, which was **detectable in January**, before filing |
| 16 | **Q4 federal estimate:** "4,000 I think" vs 3,000 on the IRS transcript; no EFTPS confirmation | Payments agreed to the transcript (federal balance +1,000; Form 2210 to re-run) |
| 17 | **Donated car** at KBB 6,000; the 1098-C shows it was sold for 2,900 (box 4a), so the deduction is limited to 2,900 | +806 / +149 |
| 18 | **Phone and internet at 100%:** client said 70% / 50% business; **Spotify** is personal | +464 / +41 and +77 / +7 |
| 19 | **Bitcoin lot C6 at $0 basis, short-term:** client says it was bought 03/2021 for 3,050 (bank line only) | Judgment: long-term, gain 2,275 (−1,312 / −104) once the bank statement is attached |
| 20 | **"No sales" (qa-14), "didn't really sell" crypto (qa-15), "no prior IRA basis" (qa-31)** | All contradicted by documents. On the IRA, the engine was **right** to use the 2024 Form 8606 (7,000) and D42; the workpaper records why the client's answer was overridden |

## D. Judgment calls and completeness (surfaced, not silently decided)

- **HVAC 11,000 at Fremont Way** was booked as a repair. The whole system was replaced, so it is a restoration and should be capitalized (Reg. 1.263(a)-3). Engine what-if: +3,633 / +896.
- **SEP contribution 18,000 exceeds the 15,214 limit.** The 2,786 excess faces a 6% excise unless withdrawn by the extended due date.
- **California AMT on the ISO exercise** (granted in CA, exercised after the move) is not computed by the engine. It is an open item.
- **Form 8867 due diligence** for CTC/ODC is not completed.
- **Filings outside the return:**
  - FBAR (foreign maximum 149,567).
  - Late 1099-NECs for two contractors.
  - Form 3520 for the 2023 inheritance, left open by the prior preparer.
  - 3,500 CA credit sitting at the FTB.
- **Prior preparer:** the 2024 home office used 300 sq ft; the room is 220 (immaterial, noted).

## E. What checked out (the engine was right)

| Check | Result |
|---|---|
| W-2 wages | Counted D10 (photo of D09) once |
| S corp basis | Rolled correctly once the 2023 K-1 arrived (independently recomputed: 2,300) |
| Corrected Meridian 1099 | Applied in v2 |
| Depreciation | Recomputed for every rental asset |
| Brightpath 260 lots | Agree to the summary |
| Carryovers | Agree to 2024 |
| Excess Social Security | Agrees |
| Backdoor Roth taxable amount | 26, agrees |
| Arithmetic | 85 of 85 engine sums re-foot |

## Net effect

If every proposed correction in sections A to C is booked, federal tax falls by about **13,100**. With the 1,000 payment correction, the v3 balance due of 4,220 becomes a refund of roughly 7,900 (about 4,300 if the HVAC is capitalized). California's refund rises by about 3,100.

The main driver is the RSU double count. It also means the 36,000 extension payment, sized from v2 in April, was far more than needed. These are rate-based estimates, as the README requires; the real figures come from the engine re-run that the workpaper flags.
