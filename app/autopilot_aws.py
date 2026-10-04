"""autopilot이 부르는 EC2 API — 이 파일 안에서만 boto3 클라이언트를 만든다 (plan.md §7).

테스트는 이 모듈을 통째로 가짜로 바꾼다. 실제 AWS는 배포 후 수동으로 확인한다.

IAM 권한은 셋뿐이다(plan.md §5.3): StartInstances(대상 인스턴스 하나), DescribeInstances,
DescribeInstanceAttribute. 끄기·만들기·지우기 권한은 없다. 그래서 여기에도 그런 함수를 두지 않는다.
"""

from __future__ import annotations

import boto3
from botocore.config import Config

from app.config import settings

_client = None


def _ec2():
    """처음 쓸 때 만든다. 모듈을 import만 하는 테스트·관리자 화면이 AWS 설정 없이도 돌게 하기 위해서다."""
    global _client
    if _client is None:
        _client = boto3.client(
            "ec2",
            region_name=settings.autopilot_region,
            # 1분 주기 루프가 AWS 쪽 지연에 오래 붙잡히지 않게 짧게 둔다. 실패하면 다음 주기에 다시 본다.
            config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
        )
    return _client


def describe(instance_id: str) -> tuple[str, str | None]:
    """(상태 이름, 사설 DNS 이름). 상태는 pending/running/stopping/stopped/shutting-down/terminated."""
    response = _ec2().describe_instances(InstanceIds=[instance_id])
    instance = response["Reservations"][0]["Instances"][0]
    return instance["State"]["Name"], instance.get("PrivateDnsName") or None


def start(instance_id: str) -> None:
    _ec2().start_instances(InstanceIds=[instance_id])


def shutdown_behavior(instance_id: str) -> str:
    """서버 안에서 poweroff할 때의 동작. "stop"이어야 한다 — "terminate"면 디스크째 사라진다."""
    response = _ec2().describe_instance_attribute(
        InstanceId=instance_id, Attribute="instanceInitiatedShutdownBehavior"
    )
    return response["InstanceInitiatedShutdownBehavior"]["Value"]
