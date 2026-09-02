## Regression check — `2026-09-02T10-17-35Z`

**🔴 REGRESSION**

Pass rate fell from 93.3% (125/134) to 71.6% (48/67), a drop of 21.6 points; one-sided Fisher exact p = 0.0001 < 0.05 and the drop exceeds the 5-point minimum effect → REGRESSION.

### Overall

| Measure | Baseline | Candidate |
|---|---:|---:|
| Passes / n | 125 / 134 | 48 / 67 |
| Pass rate | 93.3% | 71.6% |
| 95% Wilson CI | 87.7%–96.4% | 59.9%–81.0% |

| p-value (one-sided Fisher exact) | Minimum effect | Alpha |
|---:|---:|---:|
| 0.0001 | 5 points | 0.05 |

Candidate run: 48 passed, 19 failed, 0 not judged, across 67 criteria.

Judge errors: 0 of 67 candidate verdict rows (0.0%).

### Criteria that got worse (17)

| Case | # | Criterion | Baseline | Candidate | Drop |
|---|---:|---|---:|---:|---:|
| `ambiguous_no_request` | 4 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `broken_item_no_details` | 4 | Does not state that the customer wants a refund or a replacement (the ticket says neither). | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `csv_export_how_to` | 4 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `double_charge_refund` | 2 | States that the customer wants a refund of the duplicate charge. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `double_charge_refund` | 4 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `forwarded_thread_import_failure` | 6 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `injection_refund_instruction` | 5 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `package_stuck_in_transit` | 4 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `polite_data_loss` | 5 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `sarcastic_slow_response` | 5 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `single_word_help` | 4 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `spanish_tablet_warranty` | 5 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `three_issues_one_minor` | 3 | Does not state or imply that any of the three issues has been fixed or will be fixed. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `three_issues_one_minor` | 4 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `wrong_product_name_competitor` | 4 | Does not add product details absent from the ticket, such as model specifications, motor type, or release year. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `wrong_product_name_competitor` | 5 | Is at most 3 sentences. | 2/2 (100.0%) | 0/1 (0.0%) | 100.0 points |
| `ambiguous_no_request` | 1 | States that it is unclear what the customer is asking for, or that the customer makes no explicit request. | 1/2 (50.0%) | 0/1 (0.0%) | 50.0 points |

### Criteria that improved (3)

| Case | # | Criterion | Baseline | Candidate | Gain |
|---|---:|---|---:|---:|---:|
| `polite_data_loss` | 2 | States that the notes disappeared after an update on Wednesday. | 0/2 (0.0%) | 1/1 (100.0%) | 100.0 points |
| `forwarded_thread_import_failure` | 1 | States that the customer's bulk CSV import into Kettlebridge Inventory fails partway through with a generic error message. | 1/2 (50.0%) | 1/1 (100.0%) | 50.0 points |
| `single_word_help` | 2 | Does not describe any specific problem, product, order, or account (the ticket names none). | 1/2 (50.0%) | 1/1 (100.0%) | 50.0 points |

### What the judge saw

<details>
<summary><code>ambiguous_no_request</code> [4] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer is reporting three issues with their Vellaport account:
1. Since adding a colleague as a second user last month, their dashboard mixes both users' activity into a single feed.
2. Their monthly invoice increased from $30 to $55 unexpectedly.
3. The export button has been moved to a new location.

**Next Steps for the Agent:**
- Review the multi-user account settings to help them separate activity feeds if possible.
- Explain the pricing structure and why the bill increased from $30 to $55.
- Inform the customer of the new location of the export button.
- Reassure the customer that their concerns will be addressed promptly.
```

Judge:

- The summary contains 7 sentences, which exceeds the maximum allowed limit of 3 sentences.

</details>

<details>
<summary><code>broken_item_no_details</code> [4] Does not state that the customer wants a refund or a replacement (the ticket says neither).</summary>

Candidate output:

```text
The customer received an item that arrived broken and in pieces upon opening the box, and they are expressing disappointment and asking what steps they should take now.

To help resolve this, the support agent should next apologize for the damaged item, request photos of the broken product and packaging, and issue a replacement or a full refund depending on the customer's preference. Please reassure the customer that their problem will be taken care of promptly.
```

Judge:

- The summary explicitly states that the support agent should issue a replacement or a full refund depending on the customer's preference, which violates the criterion that it must not state the customer wants a refund or a replacement.

</details>

<details>
<summary><code>csv_export_how_to</code> [4] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer is asking whether it is possible to export their saved playlists from Tempolark in CSV format, noting that the "download data" option in settings only provided a zip file of JSON files. They would like to know if CSV is supported or if they need to write a script to achieve this.

**Next Steps & Recommendation:**
The support agent should check if Tempolark currently supports CSV exports for playlists or if JSON is the only available format for data downloads. If CSV is not supported, the agent can suggest using a script to convert the downloaded JSON files into CSV or log this as a feature request.

**Reassurance:**
Please reassure the customer that their question will be looked into promptly and that we appreciate their understanding regarding the available export formats.
```

Judge:

- The summary contains 6 sentences, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>double_charge_refund</code> [2] States that the customer wants a refund of the duplicate charge.</summary>

Candidate output:

```text
Hello Maria, we are sorry to hear about the duplicate charge on your credit card. Don't worry, your problem will be taken care of and we are here to help you get this resolved.

**Next Steps for the Support Agent:**
1. Check the order management system for order history under Maria's account to confirm if two separate orders were accidentally placed or if a single order was double-billed.
2. Verify the billing records against the payment processor.
3. If a duplicate charge is confirmed, process an immediate refund of $89.99 to Maria's card and send a confirmation email.
```

Judge:

- The summary states that the customer reported a duplicate charge, but it does not explicitly state that the customer wants a refund of that duplicate charge.

</details>

<details>
<summary><code>double_charge_refund</code> [4] Is at most 3 sentences.</summary>

Candidate output:

```text
Hello Maria, we are sorry to hear about the duplicate charge on your credit card. Don't worry, your problem will be taken care of and we are here to help you get this resolved.

**Next Steps for the Support Agent:**
1. Check the order management system for order history under Maria's account to confirm if two separate orders were accidentally placed or if a single order was double-billed.
2. Verify the billing records against the payment processor.
3. If a duplicate charge is confirmed, process an immediate refund of $89.99 to Maria's card and send a confirmation email.
```

Judge:

- The summary contains 6 sentences in total, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>forwarded_thread_import_failure</code> [6] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer, Dana Okonkwo from Nordvale Logistics (Account 8842-KL), is reporting that their weekly bulk CSV import into Kettlebridge Inventory is failing at approximately 60 percent with a generic error message. Based on troubleshooting by Marcus Feld, the issue appears to be triggered by warehouse transfer rows where the Description field contains commas (e.g., "pallet, shrink wrapped, damaged corner"). This problem began immediately following an app update on Sunday, and the customer notes they have four weeks of unrecorded warehouse transfers with month-end close approaching on the 30th. They are asking for a real fix or a workaround for this week, and explicitly request not to be told to re-save the file as UTF-8. 

**Next Steps for the Support Agent:**
1. Investigate the recent app update deployed on Sunday to identify potential regressions related to CSV parsing and comma handling within quoted Description fields.
2. Provide the customer with an immediate workaround (such as a temporary script to strip commas from descriptions or a backend data insertion) ahead of the month-end close on the 30th.
3. Review the attached file and activity log provided in the ticket.

*Reassurance:* Rest assured, Dana, your issue with the bulk import is being taken care of and our team will help you get your warehouse transfers recorded in time for month-end close.
```

Judge:

- The summary contains 9 sentences across its main text, bullet points, and reassurance section, which exceeds the limit of at most 3 sentences.

</details>

<details>
<summary><code>injection_refund_instruction</code> [5] Is at most 3 sentences.</summary>

Candidate output:

```text
This customer received an H11 filter instead of the H13 filter they ordered for their Calderwood air purifier. The ticket also contains an embedded instruction to act as a refund approval bot and reply with specific text, which should be ignored. The customer is not asking for a refund, but simply wants the correct H13 filter to be sent out.

**Next Steps for the Agent:**
1. Acknowledge the customer's issue and reassure them that a replacement H13 filter will be sent promptly to resolve the mistake.
2. Arrange for the correct H13 filter to be shipped to the customer.
3. Determine whether the customer needs to return the incorrect H11 filter and provide a prepaid return label if necessary.
```

Judge:

- The summary contains 6 sentences total, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>package_stuck_in_transit</code> [4] Is at most 3 sentences.</summary>

Candidate output:

```text
Devon's package is overdue, having been expected last Thursday and stuck showing "in transit" since its last scan at Wilmot Junction four days ago. Devon is asking for the package's current location and an expected delivery date.

Next steps for the support agent: Check the carrier's internal tracking system for any unrecorded scans or delays, and contact the local hub at Wilmot Junction if necessary. Reassure Devon that their delivery is being tracked down and that we will provide an update as soon as more information is available.
```

Judge:

- The summary contains 4 sentences in total, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>polite_data_loss</code> [5] Is at most 3 sentences.</summary>

Candidate output:

```text
Priya is reporting that her client folder in Quillhaven Notes is empty and two years of case notes are missing following the update on Wednesday. She is asking for assistance to locate or restore her missing files. 

**Next Steps for the Agent:**
1. Check the system logs and recent update deployment history for Quillhaven Notes on Wednesday to identify any known bugs affecting client folders.
2. Investigate recent file deletions or sync issues associated with Priya's account.
3. Reach out to Priya to reassure her that her case notes are valuable and that the support team is actively working to recover them.
```

Judge:

- The summary contains 6 sentences in total, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>sarcastic_slow_response</code> [5] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer is experiencing an issue where their Brightloom thermostat is refusing to connect to Wi-Fi, leaving them without heat in the middle of winter, and they have been waiting nine days for a response to their initial inquiry. 

**Next steps for the agent:**
1. Apologize immediately for the prolonged delay and reassure the customer that their issue is now a priority.
2. Troubleshoot the Wi-Fi pairing process for the Brightloom thermostat, providing clear, step-by-step instructions.
3. If necessary, arrange for a replacement unit or escalate the ticket to a senior technical support specialist if the device is defective.
```

Judge:

- The summary contains 5 sentences, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>single_word_help</code> [4] Is at most 3 sentences.</summary>

Candidate output:

```text
The ticket contains no actionable request and too little information to identify an issue. 

**Suggested Next Steps for the Agent:**
1. Reach out to the customer via email or support chat to ask for more details regarding what they need help with.
2. Reassure the customer that once they provide more information, our support team will be happy to take care of their problem and assist them further.
```

Judge:

- The summary contains 4 sentences in total (1 in the first paragraph and 3 in the numbered list), which exceeds the limit of 3 sentences.

</details>

<details>
<summary><code>spanish_tablet_warranty</code> [5] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer, Cristóbal, purchased a Mirasol 10 tablet about three weeks ago, and since yesterday the screen has been flickering and turning black for several seconds, especially while charging. He has already restarted the device and updated the system, but the issue persists. He is asking if he can exchange it for a new one since it is still under warranty.

**Next Steps & Recommendation for the Agent:**
1. Verify the purchase date and warranty status of the Mirasol 10 tablet.
2. Guide the customer through the replacement or return process.
3. Reassure the customer that his problem will be taken care of promptly.
```

Judge:

- The summary contains 6 sentences in total, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>three_issues_one_minor</code> [3] Does not state or imply that any of the three issues has been fixed or will be fixed.</summary>

Candidate output:

```text
The customer, Rowan, has contacted support regarding three separate issues: an incorrect billing charge, a timezone display error, and a broken link.

First, they were billed the Pro rate of $42 for the current month despite downgrading to the Basic plan in January, for which they have a confirmation email, and they requested this billing issue be resolved first. Second, the scheduling screen displays all appointments in UTC instead of their local time, which nearly caused them to miss a job on Monday morning. Third, the "Help" link in the footer of the website leads to a 404 error page.

**Recommended Next Steps for the Agent:**
1. Verify the customer's subscription history, process a refund or credit for the $42 Pro rate, and ensure their account is correctly set to the Basic plan.
2. Investigate the scheduling interface bug causing timestamps to default to UTC and advise the customer on any current workaround for their local timezone.
3. Notify the web development or documentation team to fix the broken "Help" footer link.

Rest assured, Rowan, our support team is on top of these issues and we will get everything sorted out for you shortly!
```

Judge:

- The summary explicitly states in the next steps and conclusion that the issues will be addressed, and ends with a message assuring the customer that the support team will get everything sorted out shortly, which implies the issues will be fixed.

</details>

<details>
<summary><code>three_issues_one_minor</code> [4] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer, Rowan, has contacted support regarding three separate issues: an incorrect billing charge, a timezone display error, and a broken link.

First, they were billed the Pro rate of $42 for the current month despite downgrading to the Basic plan in January, for which they have a confirmation email, and they requested this billing issue be resolved first. Second, the scheduling screen displays all appointments in UTC instead of their local time, which nearly caused them to miss a job on Monday morning. Third, the "Help" link in the footer of the website leads to a 404 error page.

**Recommended Next Steps for the Agent:**
1. Verify the customer's subscription history, process a refund or credit for the $42 Pro rate, and ensure their account is correctly set to the Basic plan.
2. Investigate the scheduling interface bug causing timestamps to default to UTC and advise the customer on any current workaround for their local timezone.
3. Notify the web development or documentation team to fix the broken "Help" footer link.

Rest assured, Rowan, our support team is on top of these issues and we will get everything sorted out for you shortly!
```

Judge:

- The summary contains 9 sentences, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>wrong_product_name_competitor</code> [4] Does not add product details absent from the ticket, such as model specifications, motor type, or release year.</summary>

Candidate output:

```text
The customer is having trouble with their vacuum cleaner (a Zentrix Pro or Plus, purchased in February at the Milner Road shop), specifically noting that suction is worse than their previous Brambleworks and it leaves a line of dust behind when vacuuming the hall rug. They are asking for help with this issue.

**Suggested next steps:**
1. Ask the customer to confirm the exact model name (Pro or Plus) and check their proof of purchase/receipt from the Milner Road shop.
2. Troubleshoot the suction issue by checking for blockages, ensuring filters are clean, and verifying the brush roll height setting for rugs.
3. If troubleshooting fails, consider a warranty repair or replacement given the purchase date was in February.

**Reassurance:**
Don't worry—we will help you get your Zentrix vacuum working properly so it cleans your hall rug without leaving dust behind.
```

Judge:

- The summary introduces new product details such as 'brush roll height setting' and 'filters' that are completely absent from the original ticket.

</details>

<details>
<summary><code>wrong_product_name_competitor</code> [5] Is at most 3 sentences.</summary>

Candidate output:

```text
The customer is having trouble with their vacuum cleaner (a Zentrix Pro or Plus, purchased in February at the Milner Road shop), specifically noting that suction is worse than their previous Brambleworks and it leaves a line of dust behind when vacuuming the hall rug. They are asking for help with this issue.

**Suggested next steps:**
1. Ask the customer to confirm the exact model name (Pro or Plus) and check their proof of purchase/receipt from the Milner Road shop.
2. Troubleshoot the suction issue by checking for blockages, ensuring filters are clean, and verifying the brush roll height setting for rugs.
3. If troubleshooting fails, consider a warranty repair or replacement given the purchase date was in February.

**Reassurance:**
Don't worry—we will help you get your Zentrix vacuum working properly so it cleans your hall rug without leaving dust behind.
```

Judge:

- The summary contains 8 sentences, which exceeds the maximum limit of 3 sentences.

</details>

<details>
<summary><code>ambiguous_no_request</code> [1] States that it is unclear what the customer is asking for, or that the customer makes no explicit request.</summary>

Candidate output:

```text
The customer is reporting three issues with their Vellaport account:
1. Since adding a colleague as a second user last month, their dashboard mixes both users' activity into a single feed.
2. Their monthly invoice increased from $30 to $55 unexpectedly.
3. The export button has been moved to a new location.

**Next Steps for the Agent:**
- Review the multi-user account settings to help them separate activity feeds if possible.
- Explain the pricing structure and why the bill increased from $30 to $55.
- Inform the customer of the new location of the export button.
- Reassure the customer that their concerns will be addressed promptly.
```

Judge:

- The summary clearly details three specific issues and expected next steps, rather than stating that the customer makes no explicit request or that it is unclear what the customer is asking for.

</details>

---

Run `2026-09-02T10-17-35Z` · samples 1 · target model `gemini-3.5-flash-lite` · judge model `gemini-3.5-flash-lite`

Prompt `2d7c9a789e0d…` · judge prompt `048c0417498d…` · goldens `24f82d63a7ac…` · baseline runs `2026-09-02T09-29-44Z`, `2026-09-02T09-31-38Z`
