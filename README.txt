README.txt

1) fetch_adopets_snapshot.py 
	output: snapshot .json

2) compare_snapshots.py
	input: snapshot1.json, snapshot2.json
	output: diff_1-2.json

3) crosscheck_removals.py
	input: diff .json
	output: enriched diff .json

4) render_diff_report.py **optional**
	input: diff .json
	output: diff .md

5) email_adopets_changes.py
	input: diff .json
	output: email report