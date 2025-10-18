#!/usr/bin/env python3
"""
Fetch all AWS resource tags in an account across regions using the
AWS Resource Groups Tagging API.

Outputs either a JSON summary of unique tag keys and their values or a CSV of
per-resource tag rows (region, service, arn, key, value).

Requirements:
  - Python 3.8+
  - boto3 (pip install -r requirements.txt)
  - Valid AWS credentials/profile configured (env vars or ~/.aws)
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from typing import DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

import boto3
from boto3.session import Session as Boto3Session
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List all AWS tags across regions via Resource Groups Tagging API",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--profile",
        help="AWS CLI/SDK profile name to use",
        default=None,
    )
    parser.add_argument(
        "--regions",
        nargs="*",
        help=(
            "Space-separated list of regions to scan. If omitted, uses opted-in regions; "
            "falls back to all available tagging API regions."
        ),
        default=None,
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "csv", "json-tree", "txt-tree"],
        default="json",
        help=(
            "Output format: 'json' (summary), 'csv' (rows), 'json-tree' (tagKey -> tagValue -> resources), "
            "or 'txt-tree' (indented parent-child view)."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path. If omitted, prints to stdout",
        default=None,
    )
    parser.add_argument(
        "--include-resources",
        action="store_true",
        help=(
            "For JSON output, also include per-resource tag rows under 'resourceTagRows'. "
            "This can be large."
        ),
    )
    return parser.parse_args()


def get_opted_in_regions(session: Boto3Session) -> List[str]:
    """Return regions the account is opted into, using EC2 DescribeRegions.

    Falls back to available regions for the tagging API if DescribeRegions is not permitted.
    """
    try:
        ec2 = session.client("ec2", region_name="us-east-1")
        response = ec2.describe_regions(AllRegions=False)
        regions = sorted(r["RegionName"] for r in response.get("Regions", []))
        if regions:
            return regions
    except Exception:
        # Intentionally swallow and fall back
        pass

    # Fallback: all available regions for the tagging API in this partition
    try:
        return sorted(session.get_available_regions("resourcegroupstaggingapi"))
    except Exception:
        return []


def parse_service_from_arn(resource_arn: str) -> str:
    """Extract the service from an ARN (arn:partition:service:region:account:resource)."""
    try:
        parts = resource_arn.split(":", 5)
        return parts[2] if len(parts) >= 6 else "unknown"
    except Exception:
        return "unknown"


def iter_region_tag_rows(
    session: Boto3Session, region: str
) -> Iterable[Tuple[str, str, str, str, str]]:
    """Yield tuples of (region, service, resource_arn, tag_key, tag_value) for a region."""
    client = session.client("resourcegroupstaggingapi", region_name=region)
    paginator = client.get_paginator("get_resources")

    try:
        for page in paginator.paginate(ResourcesPerPage=100):
            for mapping in page.get("ResourceTagMappingList", []):
                resource_arn = mapping.get("ResourceARN", "")
                service = parse_service_from_arn(resource_arn)
                for tag in mapping.get("Tags", []) or []:
                    yield (region, service, resource_arn, tag.get("Key", ""), tag.get("Value", ""))
    except (ClientError, EndpointConnectionError) as exc:
        sys.stderr.write(f"[warn] Skipping region {region}: {exc}\n")


def aggregate_tags(
    rows: Iterable[Tuple[str, str, str, str, str]]
) -> Tuple[int, Set[str], DefaultDict[str, Set[str]], List[Tuple[str, str, str, str, str]]]:
    """Aggregate unique resources, keys, and key->values from iterated rows.

    Returns:
      (unique_resource_count, unique_tag_keys, tag_key_to_values, rows_list)
    """
    unique_resource_arns: Set[str] = set()
    unique_tag_keys: Set[str] = set()
    tag_key_to_values: DefaultDict[str, Set[str]] = defaultdict(set)
    rows_list: List[Tuple[str, str, str, str, str]] = []

    for region, service, resource_arn, key, value in rows:
        rows_list.append((region, service, resource_arn, key, value))
        if resource_arn:
            unique_resource_arns.add(resource_arn)
        if key:
            unique_tag_keys.add(key)
            tag_key_to_values[key].add(value)

    return len(unique_resource_arns), unique_tag_keys, tag_key_to_values, rows_list


def write_json(
    output_path: Optional[str],
    scanned_regions: List[str],
    unique_resource_count: int,
    unique_tag_keys: Set[str],
    tag_key_to_values: Dict[str, Set[str]],
    rows_list: Optional[List[Tuple[str, str, str, str, str]]] = None,
) -> None:
    payload = {
        "scannedRegions": scanned_regions,
        "resourceCount": unique_resource_count,
        "uniqueTagKeys": sorted(unique_tag_keys),
        "tagKeyToValues": {k: sorted(list(vs)) for k, vs in sorted(tag_key_to_values.items())},
    }
    if rows_list is not None:
        payload["resourceTagRows"] = [
            {
                "region": r,
                "service": s,
                "resourceArn": arn,
                "tagKey": k,
                "tagValue": v,
            }
            for (r, s, arn, k, v) in rows_list
        ]

    text = json.dumps(payload, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def write_csv(output_path: Optional[str], rows_list: List[Tuple[str, str, str, str, str]]) -> None:
    fieldnames = ["region", "service", "resource_arn", "tag_key", "tag_value"]
    if output_path:
        outf = open(output_path, "w", newline="", encoding="utf-8")
        close_file = True
    else:
        outf = sys.stdout
        close_file = False

    try:
        writer = csv.DictWriter(outf, fieldnames=fieldnames)
        writer.writeheader()
        for region, service, arn, key, value in rows_list:
            writer.writerow(
                {
                    "region": region,
                    "service": service,
                    "resource_arn": arn,
                    "tag_key": key,
                    "tag_value": value,
                }
            )
    finally:
        if close_file:
            outf.close()


def build_tagkey_value_tree(rows_list: List[Tuple[str, str, str, str, str]]) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    tree: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    for region, service, arn, key, value in rows_list:
        key_map = tree.setdefault(key, {})
        value_list = key_map.setdefault(value, [])
        value_list.append({"region": region, "service": service, "resourceArn": arn})
    # Sort resources within each value by region/service/arn for determinism
    for key, values in tree.items():
        for value, resources in values.items():
            resources.sort(key=lambda r: (r.get("region", ""), r.get("service", ""), r.get("resourceArn", "")))
    return tree


def write_json_tree(output_path: Optional[str], rows_list: List[Tuple[str, str, str, str, str]]) -> None:
    tree = build_tagkey_value_tree(rows_list)
    text = json.dumps(tree, indent=2)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def write_txt_tree(output_path: Optional[str], rows_list: List[Tuple[str, str, str, str, str]]) -> None:
    tree = build_tagkey_value_tree(rows_list)
    lines: List[str] = []
    for key in sorted(tree.keys()):
        lines.append(f"{key}")
        values_map = tree[key]
        for value in sorted(values_map.keys(), key=lambda x: (x is None, x)):
            indented_value = "  " + (value if value is not None else "<None>")
            lines.append(indented_value)
            for res in values_map[value]:
                lines.append(f"    {res['region']} {res['service']} {res['resourceArn']}")
    text = "\n".join(lines) + ("\n" if lines else "")
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)

def main() -> None:
    args = parse_arguments()

    session = Boto3Session(profile_name=args.profile) if args.profile else Boto3Session()
    regions = args.regions if args.regions else get_opted_in_regions(session)
    if not regions:
        sys.stderr.write("[error] No regions resolved. Provide --regions or check credentials.\n")
        sys.exit(2)

    # Iterate rows across all regions
    def all_rows() -> Iterable[Tuple[str, str, str, str, str]]:
        for region in regions:
            yield from iter_region_tag_rows(session, region)

    unique_resource_count, unique_tag_keys, tag_key_to_values, rows_list = aggregate_tags(all_rows())

    if args.format == "json":
        rows_for_json = rows_list if args.include_resources else None
        write_json(
            args.output,
            scanned_regions=regions,
            unique_resource_count=unique_resource_count,
            unique_tag_keys=unique_tag_keys,
            tag_key_to_values=tag_key_to_values,
            rows_list=rows_for_json,
        )
    elif args.format == "csv":
        write_csv(args.output, rows_list)
    elif args.format == "json-tree":
        write_json_tree(args.output, rows_list)
    elif args.format == "txt-tree":
        write_txt_tree(args.output, rows_list)
    else:
        sys.stderr.write(f"[error] Unknown format: {args.format}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()


