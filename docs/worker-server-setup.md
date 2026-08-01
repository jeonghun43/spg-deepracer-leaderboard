# 평가 서버(AWS EC2) 구축 가이드

> 평가(DRFC)를 돌리는 워커 서버를 AWS EC2에 새로 만드는 절차. 운영자 노트북 대신 클라우드에서
> 24시간 평가를 처리하기 위한 것이다. **리눅스를 잘 몰라도 순서대로 따라 할 수 있게** 썼다.
>
> 웹·DB 서버(Lightsail) 쪽은 [server-access.md](server-access.md), 노트북 워커 운영은
> [operations.md](operations.md)를 본다.
>
> 최종 갱신: 2026-08-01

---

## 0. 전체 그림 — 지금 뭘 만들고 있는 건가

우리 서비스는 컴퓨터 **두 대**가 역할을 나눠서 돌아간다.

```
 [클라우드 서버 - AWS Lightsail 서울]        [평가 서버 - AWS EC2 서울]
   웹사이트 · 로그인 · 리더보드                DRFC · 평가 워커
   PostgreSQL 데이터베이스                     (모델을 실제로 달려보게 하는 곳)
   참가자가 접속하는 곳
              │                                        │
              └──────── 사설망(Tailscale) ─────────────┘
                        서로만 통하는 비밀 통로
```

- **웹 서버**는 참가자에게 보이는 쪽이다. 제출을 받아서 "대기열"에 쌓아둔다.
- **평가 서버**는 참가자에게 안 보인다. 대기열에서 하나씩 꺼내 평가하고 결과를 돌려준다.
- 이 둘이 서로 대화해야 하는데, 그 통로를 만드는 게 이 문서의 **Tailscale** 단계다.

이 문서는 평가 서버를 처음부터 만드는 절차이고, 순서는 이렇다.

| 단계 | 내용 | 이 문서 |
|---|---|---|
| 1 | EC2 인스턴스 만들기 | §1 |
| 2 | **Tailscale 설치 — 두 서버를 연결** | §2~§4 |
| 3 | Docker · DRFC 설치 | §7 |
| 4 | 워커 연결 · 평가 1건 실측 | §8.1~8.4 |
| 5 | AMI 백업 만들기 | §8.5 |
| 6 | 워커 자동 시작 등록 | §8.6 |

---

## 1. EC2 인스턴스 만들기

이미 만들어져 있다면 이 절은 건너뛴다. 아래는 2026-08-01에 실제로 만든 설정이다.

### 1.1 설정값

| 항목 | 값 | 이유 |
|---|---|---|
| 리전 | **아시아 태평양(서울) ap-northeast-2** | Lightsail과 같은 리전이어야 리전 간 전송 요금이 안 붙는다 |
| 이름 | `drfc-worker` | |
| AMI | **Ubuntu Server 22.04 LTS (HVM), SSD Volume Type** (64비트 x86) | 노트북 WSL과 같은 버전 |
| 인스턴스 유형 | **m7i.xlarge** (4vCPU / 16GB) | 평가는 GPU가 필요 없다. §1.3 참고 |
| 키 페어 | 새로 생성 (ed25519, .pem) | |
| 보안 그룹 | SSH(22)만, 소스는 **"내 IP"** | 워커는 외부에서 들어올 일이 없다 |
| 스토리지 | 루트 볼륨 **100 GiB gp3** | DRFC 도커 이미지가 수십 GB다 |
| 구매 옵션 | **스팟 인스턴스** | 온디맨드의 약 28% 가격 |
| 스팟 요청 유형 | **영구(Persistent)** | 회수돼도 자동 복귀시키기 위해 |
| 스팟 중단 동작 | **중지(Stop)** | 기본값이 "종료"라 반드시 바꿔야 한다 |
| 스팟 최대 가격 | **비워둠** | 비우면 온디맨드 가격이 상한이라 회수 확률이 가장 낮다 |

**비용** (환율 1,440원 기준): 스팟 시간당 $0.0699 → 한 달 24시간 내내 돌려도 약 7.2만원.
여기에 EBS 100GB 월 $9(1.3만원)가 더해져 **월 8.5만원 선**이다.

### 1.2 주의: 함정 세 가지

**① AMI를 잘못 고르면 스팟이 아예 안 만들어진다.**
`Spot instance requests are not supported for this AMI` 오류가 나면 라이선스가 붙은 유료 이미지를
고른 것이다. 2026-08-01에 실제로 겪었는데, Ubuntu 타일만 누르고 그 아래 AMI 드롭다운을 안 건드렸더니
`Ubuntu Server 22.04 LTS (HVM) with SQL Server 2022 Standard`가 기본 선택되어 있었다.

> **AMI 드롭다운을 직접 열어서, 이름 뒤에 아무 수식어도 안 붙은 것을 고른다.**
> `with SQL Server ...`, `Pro`, `FIPS`, `Deep Learning`이 붙은 것은 전부 유료 상품이라 스팟이 안 된다.
> 표준판에는 보통 "프리 티어 사용 가능" 딱지가 붙어 있다.

**② 인스턴스를 종료하면 100GiB가 통째로 사라진다.**
루트 볼륨의 "종료 시 삭제"가 기본 켜짐이다. DRFC 설치가 전부 날아간다.
세팅이 끝나고 **평가 1건이 성공한 직후 AMI를 한 번 떠둔다**(§7).

**③ 정리할 때 순서를 틀리면 계속 과금된다.**
§8을 참고한다.

### 1.3 왜 GPU 인스턴스가 아닌가

평가는 학습(training)과 다르다. 평가에서 GPU가 관여하는 건 카메라 이미지 렌더링뿐이고,
Gazebo 물리 연산·컨테이너 기동 시간·모델 추론은 모두 GPU와 무관하다. GPU 인스턴스는 값이 2배인데
그 2배를 회수하려면 평가 시간이 절반 이하로 줄어야 하는데 그럴 근거가 없다.
학습은 참가자가 각자 환경에서 하므로 우리 서버는 학습을 아예 하지 않는다
([spec.md](../specs/001-online-virtual-evaluation/spec.md) §참가자 범위).

### 1.4 첫 접속

키 파일은 **WSL 홈으로 복사한 뒤** 권한을 조여야 한다. `/mnt/c/...` 경로에 둔 채로 쓰면
WSL에서 항상 `0644`로 보이고 `chmod`도 먹지 않아서 ssh가 키를 거부한다.

```bash
cp /mnt/c/Users/<사용자>/Downloads/drfc-worker-key.pem ~/.ssh/
```

```bash
chmod 400 ~/.ssh/drfc-worker-key.pem
```

```bash
ssh -i ~/.ssh/drfc-worker-key.pem ubuntu@<퍼블릭IP>
```

---

## 2. Tailscale이 뭔가 — 비유로 이해하기

여기부터가 이 문서의 핵심이다. 명령어를 치기 전에 **왜 이걸 하는지**부터 읽는다.

### 2.1 문제 상황

평가 서버는 웹 서버의 **데이터베이스**에 접속해야 한다. "새로 들어온 제출 있어?" 하고 계속 물어봐야
하기 때문이다. 데이터베이스는 5432번 포트로 대화한다.

가장 쉬운 방법은 웹 서버의 5432번 포트를 인터넷에 그냥 열어두는 것이다. **그런데 이러면 안 된다.**

> 인터넷을 **온 세상 사람이 다니는 큰길**이라고 생각하자.
> 데이터베이스 포트를 인터넷에 여는 건, 그 큰길가에 우리 창고 문을 하나 내는 것과 같다.
> 문에 자물쇠(비밀번호)는 걸어뒀지만, 하루 종일 지나가면서 자물쇠를 흔들어보는 사람들이 있다.
> 그것도 사람이 아니라 **자동으로 24시간 문고리를 흔들어보는 로봇들**이다. 5432번은 그 로봇들이
> 특히 좋아하는 번호다. 언젠가 자물쇠가 열리면 대회 데이터 전부가 통째로 넘어간다.

### 2.2 Tailscale이 하는 일

Tailscale은 **우리 컴퓨터들끼리만 다닐 수 있는 비밀 지하 통로**를 뚫어주는 서비스다.

> 큰길에는 문을 **하나도 안 낸다.** 대신 우리 건물들 사이에만 지하 통로를 연결한다.
> 지나가던 로봇은 통로가 있는지조차 모른다. 입구가 큰길에 없으니까.

이 통로로 연결된 우리 컴퓨터들의 모임을 **tailnet**("테일넷")이라고 부른다. 우리끼리의 동네인 셈이다.
지금 우리 tailnet에는 이미 두 대가 들어 있다.

| 기기 | tailnet 주소 | 역할 |
|---|---|---|
| Lightsail 웹 서버 | `100.110.139.82` | 웹 · DB |
| 운영자 노트북 | (가입되어 있음) | 백업 워커 |
| **새 EC2 평가 서버** | **이번에 추가한다** | 평가 |

`100.` 으로 시작하는 주소가 **동네 안에서만 통하는 주소**다. 동네 밖에서는 이 주소를 아무리 불러도
아무도 못 찾는다. 그래서 안전하다.

### 2.3 알아둘 용어 세 개

| 말 | 비유 | 실제 의미 |
|---|---|---|
| **tailnet** | 우리 동네 | 내 계정에 묶인 컴퓨터들의 사설망 |
| **tailnet 합류(join)** | 새 건물을 우리 동네에 넣기 | 새 기기를 내 tailnet에 등록하는 것 |
| **키 만료(key expiry)** | 출입 도장의 유효기간 | 180일마다 다시 인증해야 하는 보안 장치 (§3.3에서 끈다) |

Tailscale 무료 플랜은 개인 100대까지 쓸 수 있어서 우리 규모에는 비용이 들지 않는다.

---

## 3. Tailscale 설치 — 단계별

**EC2 서버에 SSH로 접속한 상태에서** 진행한다. 프롬프트가 `ubuntu@ip-172-...:~$` 처럼
보이면 서버 안에 들어와 있는 것이다.

### 3.1 설치하기

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Tailscale 공식 설치 스크립트다. 1분 안에 끝난다.

이 스크립트는 설치와 동시에 `tailscaled`라는 **백그라운드 프로그램을 자동 시작 등록**까지 해준다.
덕분에 스팟 인스턴스가 중지됐다가 다시 켜져도 Tailscale이 알아서 다시 붙는다. 확인하려면:

```bash
systemctl is-enabled tailscaled
```

`enabled`가 나오면 정상이다.

### 3.2 tailnet에 합류하기

```bash
sudo tailscale up
```

이 명령을 치면 화면에 **주소(URL)가 하나 출력된다.** 이렇게 생겼다.

```
To authenticate, visit:

    https://login.tailscale.com/a/xxxxxxxxxxxx
```

> 서버에는 웹 브라우저가 없다. 그래서 Tailscale은 "이 주소를 다른 기기에서 열어서 네가 맞다고
> 확인해줘"라고 요청한다. **새 건물을 우리 동네에 넣기 전에 동네 주인이 도장을 찍는 절차**다.

**해야 할 일**: 출력된 URL을 복사해서 **내 노트북 브라우저에 붙여넣고 연다.**
→ 기존에 Tailscale 계정을 만들 때 쓴 방법(구글 계정 등)으로 로그인
→ "Connect" 버튼을 누른다.

서버 화면으로 돌아오면 명령이 저절로 끝나 있다. 이제 합류가 끝났다.

> **로그인 방법을 모르겠다면**: 노트북에서 https://login.tailscale.com 에 들어가 이미 로그인되어
> 있는지 확인한다. Lightsail과 노트북을 등록할 때 쓴 계정과 **반드시 같은 계정**이어야 한다.
> 계정이 다르면 다른 동네가 만들어져서 서로 안 보인다.

### 3.3 키 만료 끄기 — 빠뜨리면 안 된다

**이 단계를 건너뛰면 180일 뒤 어느 날 갑자기 평가가 전부 멈춘다.**

Tailscale은 보안을 위해 각 기기의 인증을 180일마다 만료시킨다. 사람이 쓰는 노트북은 만료돼도
다시 로그인하면 그만이지만, **서버는 옆에 사람이 없어서 아무도 다시 로그인해주지 않는다.**
그러면 통로가 조용히 끊기고, 워커는 DB에 못 붙고, 제출은 계속 대기열에 쌓이기만 한다.
원인을 모르면 찾는 데 한나절이 걸리는 종류의 고장이다.

> 도장에 유효기간이 붙어 있는데, 이 건물엔 도장 받으러 갈 사람이 없는 상황이다.
> 그래서 "이 건물은 유효기간 없음"으로 미리 지정해둔다.

**끄는 방법** (웹 브라우저에서):

1. https://login.tailscale.com/admin/machines 접속
2. 목록에서 방금 추가한 기기(보통 `ip-172-...` 같은 이름)를 찾는다
3. 그 줄 맨 오른쪽 **`...` 메뉴** 클릭
4. **"Disable key expiry"** 선택

같은 메뉴에서 **"Rename"** 으로 이름을 `drfc-worker`로 바꿔두면 나중에 알아보기 쉽다.

> Lightsail 서버와 노트북도 키 만료가 꺼져 있는지 이 김에 같이 확인해두면 좋다.

### 3.4 내 tailnet 주소 확인하기

```bash
tailscale ip -4
```

`100.x.y.z` 형태의 주소가 나온다. **이 값을 적어둔다.** 앞으로 이 서버에 접속할 때 쓰는 주소다.

여기 기록해두면 다음 사람이 편하다.

```
평가 서버 tailnet 주소: 100.93.165.104    (등록일: 2026-08-01)
```

---

## 4. 잘 연결됐는지 확인하기

### 4.1 동네에 누가 있는지 보기

```bash
tailscale status
```

Lightsail 서버와 노트북이 목록에 보이면 성공이다. 이런 식으로 나온다.

```
100.x.y.z    drfc-worker    <계정>  linux  -
100.110.139.82  drleader    <계정>  linux  active
```

### 4.2 웹 서버까지 실제로 닿는지 확인

```bash
ping -c 3 100.110.139.82
```

응답이 오면 통로가 뚫린 것이다.

### 4.3 데이터베이스 포트까지 닿는지 확인 — 가장 중요

`ping`이 된다고 DB에 붙는 건 아니다. 실제 포트를 확인한다.

```bash
nc -zv 100.110.139.82 5432
```

`succeeded!` 또는 `open`이 나오면 성공이다. `nc` 명령이 없다고 하면 설치한다.

```bash
sudo apt install -y netcat-openbsd
```

이 세 가지가 다 통과하면 Tailscale 단계는 완전히 끝난 것이다.

---

## 5. 이제부터는 tailnet 주소로 접속한다

스팟 인스턴스는 중지됐다 재시작되면 **퍼블릭 IP가 바뀐다.** 하지만 tailnet 주소는 안 바뀐다.
그래서 앞으로는 이렇게 접속한다.

```bash
ssh -i ~/.ssh/drfc-worker-key.pem ubuntu@100.x.y.z
```

매번 AWS 콘솔에 들어가 IP를 확인할 필요가 없어진다. 노트북에도 Tailscale이 깔려 있어야
이게 되는데, 이미 깔려 있다.

> 참고: 보안 그룹의 SSH 규칙은 "내 IP"로 되어 있어서, 집이나 학교를 옮겨 공인 IP가 바뀌면
> 퍼블릭 IP로는 못 들어간다. 그때도 tailnet 주소로는 들어가진다. 이게 Tailscale을 먼저 까는
> 또 다른 이유다.

---

## 6. 자주 나는 문제

| 증상 | 원인과 해결 |
|---|---|
| `tailscale up`을 쳤는데 URL이 안 나온다 | 이미 로그인된 상태일 수 있다. `tailscale status`로 확인한다 |
| `tailscale status`에 다른 기기가 안 보인다 | **계정이 다르다.** 기존 tailnet과 다른 계정으로 로그인한 것이다. `sudo tailscale logout` 후 §3.2를 올바른 계정으로 다시 한다 |
| `ping`은 되는데 `nc`가 실패한다 | Lightsail 쪽 DB가 안 떠 있거나 tailnet 주소에 바인딩이 안 된 것이다. [server-access.md](server-access.md)를 보고 웹 서버에서 `docker compose ps`로 확인한다 |
| 잘 되다가 어느 날 갑자기 워커가 DB에 못 붙는다 | **키 만료(§3.3)를 안 껐을 가능성이 가장 높다.** 관리 콘솔에서 해당 기기가 "Expired" 상태인지 확인한다 |
| 서버 재시작 후 Tailscale이 안 붙어 있다 | `sudo systemctl status tailscaled`로 확인. `sudo systemctl enable --now tailscaled` |

---

## 7. DRFC 설치

### 7.1 절차

```bash
sudo apt update && sudo apt install -y git
```

```bash
git clone https://github.com/aws-deepracer-community/deepracer-for-cloud.git
```

```bash
cd ~/deepracer-for-cloud && ./bin/prepare.sh
```

`prepare.sh`는 **우리 전용 서버이므로 돌려도 안전하다.** (연구실 공용 서버 같은 곳에서는 절대
돌리면 안 된다. 도커와 NVIDIA 드라이버를 통째로 갈아엎어서 다른 사용자의 환경을 깨뜨린다.)
GPU가 없으므로 드라이버 단계는 알아서 건너뛴다. 끝나면 재부팅이나 재로그인을 요구한다.

재접속한 뒤:

```bash
cd ~/deepracer-for-cloud && ./bin/init.sh -c local -a cpu
```

`Creating default minio credentials in AWS profile 'minio'` 가 출력되면 제대로 간 것이다.

### 7.2 ⚠️ 가장 큰 함정 — `-c local`이 실제로 적용됐는지 확인한다

**2026-08-01에 실제로 여기서 막혔다.** EC2에서는 DRFC의 클라우드 자동 감지가 `aws`로 잡힐 수 있는데,
그러면 `init.sh`가 전혀 다른 분기를 탄다.

```
if [[ "${OPT_CLOUD}" == "aws" ]]; then
    sedi "s/<LOCAL_PROFILE>/default/g" $INSTALL_DIR/system.env
```

`aws` 분기로 가면 **`[minio]` 프로필을 만들지 않고**, `activate.sh`도 `DR_MINIO_COMPOSE_FILE`을
빈 값으로 둬서 **MinIO 스택을 아예 배포하지 않는다.** 그 결과 `dr-upload-custom-files`가
로컬 MinIO가 아니라 **진짜 AWS S3**의 `bucket`이라는 남의 버킷을 찔러서 `AccessDenied`가 난다.

**증상 세 가지가 동시에 나타나면 이 문제다.**

| 확인 명령 | 정상 | 이 문제일 때 |
|---|---|---|
| `aws configure --profile minio get aws_access_key_id` | 값이 나옴 | 프로필 없음 |
| `docker stack ls` | `s3` 스택이 보임 | 비어 있음 |
| `dr-upload-custom-files` | 정상 업로드 | `AccessDenied` |

**해결**: 스웜을 내리고 `-c local`로 다시 초기화한다. `init.sh`는 스웜이 이미 있으면
`Swarm exists. Exiting.`으로 중간에 끊겨서 뒷부분(오버레이 네트워크 생성)을 건너뛰기 때문에,
반드시 먼저 내려야 한다.

```bash
docker swarm leave --force
```

```bash
cd ~/deepracer-for-cloud && ./bin/init.sh -c local -a cpu
```

### 7.3 평가 조건을 노트북과 똑같이 맞춘다

`init.sh`가 만드는 기본 `run.env`를 그대로 쓰면 **평가 조건이 달라져 대회 기록을 서로 비교할 수 없게
된다.** [handover.md](handover.md)의 "평가 기준이 저장소 밖 설정" 경고가 정확히 이 상황을 가리킨다.

손으로 옮겨 적지 말고 **파일째 복사한다.** `init.sh`가 `system.env`를 템플릿에서 새로 만들기 때문에
**반드시 `init.sh` 다음에** 복사해야 한다. 노트북(WSL)에서 실행한다.

```bash
scp -i ~/.ssh/drfc-worker-key.pem ~/deepracer-for-cloud/run.env ~/deepracer-for-cloud/system.env ubuntu@100.93.165.104:~/deepracer-for-cloud/
```

두 파일 모두 기기별로 달라지는 값이 없어서 통째로 복사해도 안전하다. **MinIO 자격증명
(`~/.aws/credentials`)은 복사하면 안 된다** — 기기마다 `init.sh`가 새로 만든다.

복사되는 평가 조건은 다음과 같다. 대회 중에는 절대 바꾸지 않는다.

| 설정 | 값 | 의미 |
|---|---|---|
| `DR_WORLD_NAME` | Vegas_track | 대회 트랙 |
| `DR_RACE_TYPE` | TIME_TRIAL | 타임트라이얼 |
| `DR_EVAL_NUMBER_OF_TRIALS` | 3 | 3바퀴 |
| `DR_EVAL_CHECKPOINT` | best | 제출 모델의 best 체크포인트로 평가 |
| `DR_EVAL_OFF_TRACK_PENALTY` | 5.0 | 트랙 이탈 패널티 |
| `DR_EVAL_COLLISION_PENALTY` | 5.0 | |
| `DR_EVAL_SAVE_MP4` | True | 영상 저장 (리더보드에 필요) |
| `DR_LOCAL_S3_MODEL_PREFIX` | rl-deepracer-sagemaker | 워커 코드가 이 경로를 읽는다 |
| `DR_SIMAPP_VERSION` | 6.0.4-**cpu** | CPU 전용 이미지. GPU가 필요 없다는 근거 |

### 7.4 MinIO 이미지 버전 고정

복사해온 `system.env`의 `DR_MINIO_IMAGE=latest`를 아래로 바꾼다.

```
DR_MINIO_IMAGE=RELEASE.2022-10-24T18-35-07Z
```

DRFC 코드가 이 값이 비었을 때 쓰는 기본값이 바로 이 버전이다(`bin/activate.sh`). 개발자들이 검증한
버전이라는 뜻이고, `latest`로 두면 설치 시점마다 다른 MinIO를 받아 노트북에서는 되는데 새 서버에서는
안 되는 상황이 생길 수 있다.

### 7.5 🔒 이 서버에 AWS 자격증명을 두지 않는다

평가 워커는 **진짜 AWS 자격증명이 전혀 필요 없다.** 저장소는 로컬 MinIO, DB는 Tailscale,
웹 서버는 HTTP 토큰으로 붙기 때문이다. 스팟 인스턴스에 계정 키를 올려두면 유출 시 계정 전체가
위험해진다.

```bash
rm -f ~/.aws/credentials ~/.aws/config
```

(이 명령은 `init.sh`가 `[minio]` 프로필을 만들기 **전에** 실행한다. 이미 만들었다면
`[default]` 프로필만 지우고 `[minio]`는 남긴다. MinIO 자격증명은 로컬 전용이라 무해하다.)

### 7.6 확인

```bash
source bin/activate.sh run.env
```

```bash
docker stack ls
```

`s3` 스택이 보이면 성공이다. 이어서:

```bash
dr-upload-custom-files
```

---

## 8. 워커 연결과 백업

### 8.1 DB 연결 확인 (워커 실행 전 필수)

```bash
nc -zv 100.110.139.82 5432
```

`succeeded!`가 나와야 한다. 안 되면 §4를 다시 본다.

### 8.2 저장소 클론과 `.env`

[operations.md](operations.md)의 워커 `.env` 형식을 그대로 쓰되, `DATABASE_URL`의 호스트를
Lightsail tailnet 주소(`100.110.139.82`)로 둔다. `WORKER_TOKEN`이 설정되면 워커가 자동으로
http 전송 모드로 동작한다.

### 8.3 워커 첫 실행 — ⚠️ 로그가 조용한 게 정상이다

```bash
cd ~/spg-deepracer-leaderboard && bash worker/run_worker.sh
```

다음 두 줄이 나오면 DRFC 연동과 DB 접속이 모두 정상이다.

```
[run_worker] DR_LOCAL_S3_BUCKET=bucket (환경변수 확인됨)
워커 시작 (worker_id=ip-172-31-..., drfc_dir=/home/ubuntu/deepracer-for-cloud)
```

**평가가 시작되면 워커 로그가 최대 30분간 아무것도 출력하지 않는다. 멈춘 것이 아니다.**
`worker/drfc.py`의 `run_evaluation_blocking()`이 `subprocess.run(..., capture_output=True)`로
`run_evaluation.sh`를 실행하기 때문에, 그 안의 `[run_evaluation]` 진행 로그가 전부 캡처되어
화면에 나오지 않는다. 출력은 **실패했을 때만** 예외 메시지에 꼬리가 붙어 나온다.

> 2026-08-01에 실제로 이것 때문에 멀쩡히 평가 중이던 워커를 Ctrl+C로 죽였다.
> 조용하다고 죽이지 말 것.

평가 진행 상황은 워커 로그가 아니라 **다른 창에서 docker로 본다.**

```bash
docker stack ps deepracer-eval-0 --filter desired-state=running
```

```bash
docker service logs -f deepracer-eval-0_robomaker
```

`robomaker`와 `rl_coach`가 `Running`으로 보이면 시뮬레이션이 실제로 돌고 있는 것이다.

또한 모델 업로드 중에 아래 경고가 뜰 수 있는데, urllib3가 `except`로 잡아서 로그만 남기고
정상 진행하는 것이므로 무시해도 된다(`[ERROR]`가 아니라 `[WARNING]`인지 확인할 것).

```
Failed to parse headers ... MissingHeaderBodySeparatorDefect
```

### 8.4 평가 1건 실측

`run_evaluation.sh`가 출력하는 `평가 완료 (N초 경과)` 값과 metrics의
`elapsed_time_in_milliseconds` 합계를 비교해 **기동 오버헤드 대 시뮬 시간 비율**을 기록한다.
이 값으로 [config.py](../app/config.py)의 `eval_minutes_estimate`를 갱신해야 참가자에게 보여주는
예상 대기시간이 맞아떨어진다. (노트북 기준값은 10분이었다.)

### 8.5 AMI 백업 만들기 — 평가가 성공한 직후에 한다

인스턴스를 종료하면 루트 볼륨 100GiB가 통째로 사라져서 여기까지 한 설치가 전부 날아간다.
AMI를 떠두면 같은 상태의 서버를 언제든 다시 띄울 수 있다.

> EC2 → 인스턴스 → `drfc-worker` 선택 → **작업** → **이미지 및 템플릿** → **이미지 생성**

- 이미지 이름: `drfc-worker-YYYYMMDD`
- **재부팅하도록 둔다.** 콘솔 버전에 따라 라벨이 반대로 쓰여 있으니 주의한다 —
  **"인스턴스 재부팅"이면 체크된 상태 유지**(기본값), "재부팅 안 함"이면 체크 해제.
  재부팅 없이 이미지를 뜨면 파일이 쓰이다 만 상태로 굳어서, 복원했을 때 DRFC가 미묘하게 깨져 있을
  수 있고 원인을 찾기 매우 어렵다. 재부팅은 중지·종료가 아니므로 **스팟 요청에 영향이 없다**
- 상태가 "사용 가능"이 되면 완료된다. 몇 분 걸린다
- 비용은 실제 사용된 용량만 과금된다. 30GB 정도면 월 2천원 수준이다

이 AMI는 계정 내 비공개다. `.env`의 `WORKER_TOKEN`이 함께 들어가므로 **외부에 공유하지 않는다.**

### 8.6 워커를 systemd 서비스로 등록

SSH 세션을 닫으면 워커가 같이 죽는다. 또 스팟이 회수됐다 재시작됐을 때 사람이 없어도 워커가
자동으로 살아나야 한다. systemd 서비스로 등록하면 두 문제가 함께 해결된다.

유닛 파일을 만든다.

```bash
sudo nano /etc/systemd/system/drfc-worker.service
```

```ini
[Unit]
Description=DeepRacer evaluation worker
After=docker.service network-online.target tailscaled.service
Requires=docker.service
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/spg-deepracer-leaderboard
Environment=HOME=/home/ubuntu
ExecStart=/bin/bash /home/ubuntu/spg-deepracer-leaderboard/worker/run_worker.sh
Restart=always
RestartSec=30
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
```

각 항목이 왜 필요한지:

| 항목 | 이유 |
|---|---|
| `WorkingDirectory` | `.env`를 상대경로(`env_file=".env"`)로 읽기 때문에 저장소 루트에서 실행돼야 한다 |
| `Environment=HOME` | `run_worker.sh`가 `$HOME/deepracer-for-cloud`를 기본값으로 쓰고, boto3도 `~/.aws`를 찾는다 |
| `After=docker.service` | MinIO가 Docker Swarm 서비스라 도커가 먼저 떠야 한다 |
| `Restart=always` + `RestartSec=30` | 도커나 MinIO가 아직 준비 안 됐으면 실패하고 30초 뒤 다시 시도한다 |
| `TimeoutStopSec=90` | 정지 시 정리할 시간을 준다 |

등록하고 시작한다.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now drfc-worker
```

```bash
sudo systemctl status drfc-worker
```

`active (running)`이면 성공이다.

**로그는 이제 `journalctl`로 본다.**

```bash
journalctl -u drfc-worker -f
```

지난 100줄만 보려면:

```bash
journalctl -u drfc-worker -n 100 --no-pager
```

**⚠️ 평가 중에 `systemctl stop`을 하지 않는다.** §8.3과 같은 이유로 그 제출이 `running`에 갇힌다.
멈추기 전에 `docker stack ls`로 `deepracer-eval-0`이 없는지 확인한다.

> 워커가 시작할 때 `recover_stale_running`이 "평가중"에 멈춰 있던 제출을 대기열로 되돌린다.
> 다만 그 기준이 **시작한 지 35분이 지난 건**이라, 스팟이 회수됐다가 35분 안에 복귀하면
> 그 제출은 자동으로 풀리지 않는다. §8.7을 본다.

### 8.7 ⚠️ 스팟 회수 시 남는 구멍 (미해결)

`recover_stale_running`은 **워커가 시작할 때 딱 한 번만** 실행된다(`worker/run.py`의 `main()`).
그리고 되돌리는 대상은 `started_at`이 35분 이상 지난 건뿐이다. 그래서 이런 경우가 생긴다.

1. 평가 시작 5분 뒤 스팟이 회수되어 인스턴스가 중지된다
2. 10분 뒤 AWS가 인스턴스를 다시 켜고, systemd가 워커를 시작한다
3. 그 제출의 `started_at`은 15분 전 → 35분 기준에 못 미쳐 **되돌려지지 않는다**
4. 이후 `recover_stale_running`은 다시 호출되지 않으므로 **영구히 `running`에 갇힌다**

그 팀은 "이전 제출의 결과가 아직 나오지 않았습니다"에 막혀 새 모델을 못 올린다.

**당장의 대처**: 스팟 회수가 있었던 날은 아래로 갇힌 제출이 있는지 확인하고 수동으로 되돌린다.

```sql
SELECT id, worker_id, started_at FROM submissions WHERE status = 'running';
```

```sql
UPDATE submissions SET status='queued', worker_id=NULL, started_at=NULL WHERE id=<제출ID>;
```

**근본 해결(권장)**: 워커가 시작할 때 **자기 `worker_id`로 잡혀 있는 `running`은 시간과 무관하게
즉시 되돌리도록** 고친다. 워커가 방금 시작했다는 것은 자기가 처리 중이던 작업이 이미 죽었다는
뜻이므로, 시간 기준 없이 회수해도 안전하다(다른 워커의 것은 기존 35분 기준을 유지한다).

> **워커는 이 서버에서 1개만 실행한다.** 평가 영상이 S3의 고정 경로에 덮어써지기 때문에
> 한 서버에서 2개를 돌리면 영상이 섞인다. 처리량이 부족하면 노트북을 두 번째 워커로 켜는 것이
> 가장 안전하다(다른 기기라 경로가 겹치지 않는다). 같은 서버에서 병렬로 돌리려면
> `DR_RUN_ID`와 `DR_LOCAL_S3_MODEL_PREFIX`를 분리한 별도 `run.env`가 필요하고,
> `worker/run.py`의 `WORKER_ID`가 호스트명 고정이라 한 줄 수정도 필요하다.

---

## 9. 대회가 끝나면 — 정리 절차

**순서를 반드시 지킨다.** 반대로 하면 요금이 계속 나간다.

1. **먼저 스팟 요청을 취소한다** — EC2 → 왼쪽 메뉴 **스팟 요청** → 해당 요청 선택 → 취소
2. 그 다음 인스턴스를 종료한다

스팟 요청을 "영구(Persistent)"로 만들었기 때문에, 요청이 살아 있는 상태에서 인스턴스만 종료하면
**AWS가 자동으로 새 인스턴스를 다시 띄운다.** 요청을 먼저 취소하면 중지 상태인 인스턴스도 함께
정리된다.

3. 남은 EBS 볼륨과 AMI·스냅샷도 확인해 지운다 — 인스턴스를 지워도 이것들은 남아서 계속 과금된다
   (AMI는 다음 대회에 재사용할 거라면 남겨둔다. 월 2천원 수준이다)
4. Tailscale 관리 콘솔에서 해당 기기를 삭제한다

> 사고 방지용으로 AWS Billing → **Budgets**에 월 예산 알림을 하나 걸어두는 것을 권한다.

---

## 참고

- [Tailscale 리눅스 설치 문서](https://tailscale.com/kb/1031/install-linux)
- [Tailscale 키 만료 문서](https://tailscale.com/kb/1028/key-expiry)
- [cloud-migration.md](../specs/001-online-virtual-evaluation/cloud-migration.md) — 왜 Tailscale을
  택했는지, 다른 방법(포트 개방 · SSH 터널)을 왜 버렸는지에 대한 설계 근거
