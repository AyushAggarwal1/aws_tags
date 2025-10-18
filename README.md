AWS Tags Exporter
=================

Fetch all AWS resource tags in your account across regions using the AWS Resource Groups Tagging API.

Prerequisites
-------------
- Python 3.8+
- AWS credentials configured (env vars or `~/.aws/{credentials,config}`)

Install
-------
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Usage
-----
JSON summary (unique keys and values):
```bash
python get_all_aws_tags.py --profile myprofile --format json -o tags.json
```

Include per-resource rows inside JSON (can be large):
```bash
python get_all_aws_tags.py --profile myprofile --format json --include-resources -o tags_with_rows.json
```

CSV of all resource tag rows:
```bash
python get_all_aws_tags.py --profile myprofile --format csv -o tags.csv
```

Parent-child view (tagKey -> tagValue -> resources) as JSON:
```bash
python get_all_aws_tags.py --profile myprofile --format json-tree -o tag_tree.json
```

Parent-child view as indented text:
```bash
python get_all_aws_tags.py --profile myprofile --format txt-tree -o tag_tree.txt
```

Limit to specific regions:
```bash
python get_all_aws_tags.py --regions us-east-1 us-west-2
```

Notes
-----
- By default, the script scans opted-in regions (falls back to all tagging API regions if necessary).
- You need `tag:GetResources` permissions and access to listed regions.

