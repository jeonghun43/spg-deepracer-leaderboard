# 평가 서버(AWS EC2) 구축 가이드

> 📌 **주소·경로는 [handover.md](handover.md) §0 '내 환경 값' 표가 출처다.** 명령에 `<사용자>`가
> 보이면 본인 Windows 사용자명으로 바꿔서 실행한다. 서버를 새로 만들어 IP가 바뀌었다면 §0을
> 먼저 고치고 이 문서도 함께 갱신한다.

> 평가(DRFC)를 돌리는 워커 서버를 AWS EC2에 새로 만드는 절차. 운영자 노트북 대신 클라우드에서
> 24시간 평가를 처리하기 위한 것이다. **리눅스를 잘 몰라도 순서대로 따라 할 수 있게** 썼다.
>
> 웹·DB 서버(Lightsail) 쪽은 [server-access.md](server-access.md), 노트북 워커 운영은
> [operations.md](operations.md)를 본다.
>
> 최종 갱신: 2026-10-04 (§8.13 자동 켜기·끄기 추가, §8.8 폐기 표시, §9.4 비용)

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
| 7 | **자동 켜기·끄기** — IAM 사용자, 자동 끄기 타이머 (2026-10-04 추가) | §8.13 |

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
git clone https://github.com/aws-deepracer-community/deepracer-for-cloud
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

### 7.3 평가 조건을 노트북과 똑같이 맞춘다 - (노트북이 대회 환경으로 세팅되어있는 상태여야함)

세팅되어 있지 않다면 아래 내용을 따라갈 필요 없이 run.env를 대회 환경에 맞게 수정하면 됨


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
| `DR_WORLD_NAME` | reInvent2019_track | 대회 트랙 |
| `DR_RACE_TYPE` | TIME_TRIAL | 타임트라이얼 |
| `DR_EVAL_NUMBER_OF_TRIALS` | 3 | 3바퀴 |
| `DR_EVAL_CHECKPOINT` | best | 제출 모델의 best 체크포인트로 평가 |
| `DR_EVAL_OFF_TRACK_PENALTY` | 3.0 | 트랙 이탈 패널티 (2026-08-01에 5.0에서 낮춤) |
| `DR_EVAL_MAX_RESETS` | 15 | 이탈 후 최대 재시작 횟수 (2026-08-01에 100에서 낮춤) |
| `DR_EVAL_COLLISION_PENALTY` | 5.0 | |
| `DR_EVAL_SAVE_MP4` | True | 영상 저장 (리더보드에 필요) |
| `DR_LOCAL_S3_MODEL_PREFIX` | rl-deepracer-sagemaker | 워커 코드가 이 경로를 읽는다 |
| `DR_SIMAPP_VERSION` | 6.0.4-**cpu** | CPU 전용 이미지. GPU가 필요 없다는 근거 |

> ⚠️ **이 표는 사본이고, 진짜 값은 각 평가 서버의 `run.env`에 있다.** 결정 기록은
> [spec.md](../specs/001-online-virtual-evaluation/spec.md) §8이다. 2026-08-01에 이탈 패널티와
> 최대 리셋을 낮췄을 때 이 표만 갱신되지 않아, 2026-09-07 참가자 기록 조사에서 트랙 이름까지
> 틀린 채로 남아 있는 것이 드러났다. **표를 믿지 말고 서버에서 직접 확인한다.**
>
> ```bash
> grep -E "DR_WORLD_NAME|DR_EVAL_" ~/deepracer-for-cloud/run.env
> ```

### 7.4 MinIO 이미지 버전 고정

복사해온 `system.env`의 `DR_MINIO_IMAGE=latest`를 아래로 바꾼다.

```
DR_MINIO_IMAGE=RELEASE.2025-09-07T16-13-09Z
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

### 8.2 저장소 클론 · 파이썬 환경 · `.env` (2026-10-01 보강)

> 2026-10-01 평가 서버를 새로 만들 때 이 절에 실제 명령이 없어서 막혔다. 지난번 서버는 손으로
> 만들고 기록을 남기지 않았기 때문이다. 이때 "웹 서버에 들어가서 `run_worker.sh`를 돌려야 하나?"
> 하는 혼동도 있었다.

**워커는 이 평가 서버에서만 돈다.** 웹 서버(`~/drleader`)에도 tar로 올라간 `worker/` 폴더가 있지만
거기서는 실행할 수 없다. `run_worker.sh`는 시작하자마자 `~/deepracer-for-cloud/bin/activate.sh`를
불러오는데, 웹 서버에는 DRFC가 없다. 2GB 서버라 시뮬레이터를 돌릴 수도 없다. 그래서 새 평가
서버를 만들면 **워커 코드도 이 서버에 새로 받아야 한다.**

#### ① 저장소 받기

공개 저장소라 인증 없이 받아진다. 운영 코드는 **`main` 브랜치**를 쓴다. clone하면 기본으로
`main`이 받아진다.

```bash
cd ~ && git clone https://github.com/jeonghun43/spg-deepracer-leaderboard.git
```

```bash
cd ~/spg-deepracer-leaderboard && git branch --show-current
```

`main`이 나와야 한다.

- **경로를 바꾸지 않는다.** §8.6의 systemd 유닛이 `/home/ubuntu/spg-deepracer-leaderboard`를 그대로
  가리킨다.
- 이후 코드 갱신은 이 폴더에서 `git pull`로 한다(§9.3 표). 웹 서버의 tar 방식과 다르다
  ([server-access.md](server-access.md) §5).

#### ② 파이썬 환경 만들기

```bash
cd ~/spg-deepracer-leaderboard && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

- `run_worker.sh`는 마지막에 저장소의 **`.venv/bin/python`을 직접 실행한다**
  ([run_worker.sh](../worker/run_worker.sh) 마지막 줄). 그래서 폴더 이름은 반드시 `.venv`여야 한다.
  `venv`나 시스템 파이썬으로는 워커가 뜨지 않는다.
- `python3-venv`는 §7.1의 `prepare.sh`가 이미 설치했다.
- [operations.md](operations.md) "사전 준비"에도 같은 명령이 있지만, 그쪽은 노트북(WSL) 경로
  (`/mnt/c/...`)다.

#### ③ `.env` 만들기 — 워커가 읽는 키는 3개뿐이다

```bash
nano ~/spg-deepracer-leaderboard/.env
```

```
DATABASE_URL=postgresql+psycopg2://drleader:<비밀번호>@100.110.139.82:5432/drleader
WEB_BASE_URL=https://spg-deepracer.doublejeong.com
WORKER_TOKEN=<웹 서버 .env의 WORKER_TOKEN과 같은 값>
```

| 키 | 값을 어디서 가져오나 | 틀리면 |
|---|---|---|
| `DATABASE_URL` | `<비밀번호>`는 **웹 서버 `.env`의 `POSTGRES_PASSWORD`**. 운영 DB 계정은 `drleader`로 고정이다([docker-compose.prod.yml](../docker-compose.prod.yml)의 `web` 서비스 `DATABASE_URL`). 호스트는 반드시 Lightsail의 tailnet 주소 `100.110.139.82` | 워커가 시작하자마자 DB 접속 오류로 죽는다 |
| `WEB_BASE_URL` | 대회 사이트 주소 그대로 | 모델 다운로드부터 실패한다 |
| `WORKER_TOKEN` | **웹 서버 `.env`의 `WORKER_TOKEN`과 한 글자도 다르지 않게** | 모델 다운로드가 **`404 Not Found`** 로 실패한다. 토큰이 틀려도 401이 아니라 404인 것은 의도된 동작이다. 내부 경로가 있다는 것 자체를 숨기려는 것이다([app/routers/internal.py](../app/routers/internal.py)의 `require_worker`) |

`WORKER_TOKEN`이 있으면 워커는 자동으로 **http 전송 모드**로 동작한다. 모델을 웹 서버에서 내려받고,
영상·metrics를 웹 서버로 올린다. 비어 있으면 웹과 같은 디스크를 공유하던 옛 방식으로 동작하고,
이 서버에서는 실패한다.

**값은 웹 서버에 접속해서 직접 보고 옮긴다.** 채팅이나 문서에 붙여 넣지 않는다(CLAUDE.md §4).

```bash
grep -E '^(POSTGRES_PASSWORD|WORKER_TOKEN)=' ~/drleader/.env
```

(웹 서버에서 실행한다. 자기 터미널에만 출력된다.)

다 쓴 뒤 다른 계정이 읽지 못하게 권한을 조인다.

```bash
chmod 600 ~/spg-deepracer-leaderboard/.env
```

**[왜 `.env.example`의 다른 키는 넣지 않나]** `.env.example`은 **웹 서버용 템플릿**이라 키가 많다.
하지만 워커 코드(`worker/`와 워커가 불러오는 `app/` 모듈)가 `.env`에서 바꿔 쓰는 설정은
`database_url`, `worker_token`, `web_base_url` 세 개뿐이다. 나머지(`storage_dir`, `online_eval_laps` 등)는
코드 기본값을 쓴다. 2026-10-01에 `settings.` 사용처를 전부 검색해 확인했다.

| `.env.example`의 키 | 누가 쓰나 | 워커 서버에 넣으면 |
|---|---|---|
| `SESSION_SECRET`, `SESSION_HTTPS_ONLY`, `SESSION_MAX_AGE_SECONDS` | 웹(로그인 세션) | 효과 없음. **세션 서명 키만 불필요하게 노출된다** |
| `ADMIN_LOGIN_PATH`, `ADMIN_LOGIN_*`, `TEAM_LOGIN_*` | 웹(로그인 화면·잠금) | 효과 없음. **숨긴 관리자 경로가 노출된다** |
| `DAILY_SUBMISSION_LIMIT` | 웹(제출 접수) | **효과 없음.** 대회 중 한도를 바꾸려면 웹 서버 `.env`를 고친다 |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | 웹 서버의 DB 컨테이너 | 효과 없음. 비밀번호는 `DATABASE_URL` 안에만 있으면 된다 |

넣어도 워커가 깨지지는 않는다. 모르는 키는 무시하기 때문이다(`app/config.py`의 `extra="ignore"`).
그래도 넣지 않는 이유는 **이 서버의 `.env`가 AMI에 그대로 담기기 때문이다**(§8.5). 워커 서버나
AMI가 새더라도 웹의 세션 서명 키와 관리자 경로까지 함께 새지 않게, 워커에 필요한 값만 둔다.

#### ④ 확인

| 확인 | 명령 | 기대값 |
|---|---|---|
| 파이썬 환경 | `ls ~/spg-deepracer-leaderboard/.venv/bin/python` | 파일이 있다 |
| `.env` 키 (값은 안 보인다) | `grep -oE '^[A-Z_]+=' ~/spg-deepracer-leaderboard/.env` | `DATABASE_URL=` `WEB_BASE_URL=` `WORKER_TOKEN=` 세 줄 |
| 워커가 실제로 쓸 웹 주소 | `cd ~/spg-deepracer-leaderboard && .venv/bin/python -c "from app.config import settings; print(settings.web_base_url)"` | `https://spg-deepracer.doublejeong.com` (**`http://localhost:8000`이면 `WEB_BASE_URL`이 빠진 것이다**) |
| DB까지 닿는가 | §8.1의 `nc -zv 100.110.139.82 5432` | `succeeded!` |

넷 다 통과하면 §8.3으로 간다.

> **2026-10-01 실제로 겪음 — `WEB_BASE_URL`을 빠뜨리면 DB는 붙는데 전송만 실패한다.**
> 웹 서버용 키를 걸러내다가 `WEB_BASE_URL`까지 지웠다. 워커는 코드 기본값
> `http://localhost:8000`(`app/config.py`)으로 모델을 받으러 갔고, 평가 서버에는 웹이 없으니
> 다음 로그가 30초마다 반복됐다.
>
> ```
> 파일 전송 실패 — 대기열로 되돌립니다: submission=… 웹 서버에 연결하지 못했습니다: [Errno 111] Connection refused
> ```
>
> 이 기본값은 웹과 워커가 한 기기에 있던 시절의 값이라 **빠뜨려도 오류 없이 그럴듯하게 동작하는 척한다.**
> 실패한 제출은 "실패"로 기록되지 않고 대기열로 되돌아가므로 잃는 것은 없다. `.env`를 고친 뒤
> `sudo systemctl restart drfc-worker`로 재시작하면 반영된다(설정은 기동할 때 한 번만 읽는다).
> 웹 주소를 `http://100.110.139.82:8000`처럼 IP와 포트로 넣어도 같은 오류가 난다. 운영 웹
> 컨테이너는 8000번을 서버 밖으로 열지 않기 때문이다(`docker-compose.prod.yml`의 `expose`).

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

**AMI를 뜨기 전에 세 가지를 확인한다.**

| 확인 | 명령 | 기대값 |
|---|---|---|
| 코드가 최신인가 | `cd ~/spg-deepracer-leaderboard && git log --oneline -1` | 최신 커밋 |
| 평가가 안 돌고 있는가 | `docker stack ls` | `deepracer-eval-0` 없음 |
| 워커가 자동 시작 등록됐나 | `sudo systemctl is-enabled drfc-worker` | `enabled` |

**AMI 생성에 따른 재부팅은 스팟 회수·복귀의 리허설이다.** 재부팅이 끝난 뒤 사람이 아무것도 하지
않았는데 `systemctl status drfc-worker`가 `active (running)`이고 `docker stack ls`에 `s3`가
보이면, 스팟이 회수됐다 돌아와도 자동 복구된다는 것이 검증된 것이다.

**옛 AMI를 지울 때는 두 단계를 모두 거친다.** AMI를 등록 취소만 하면 **뒤에 있는 EBS 스냅샷이
남아 계속 과금된다.** 순서도 지켜야 한다 — 스냅샷을 먼저 지우려 하면 AMI가 참조 중이라 거부된다.

1. EC2 → **AMI** → 옛 이미지 선택 → 작업 → **AMI 등록 취소**
2. EC2 → **스냅샷** → 설명에 그 AMI ID가 적힌 스냅샷 선택 → 작업 → **스냅샷 삭제**

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

**⚠️ 평가 중에 `systemctl stop`을 하지 않는다.** §8.3과 같은 이유로(로그가 조용해서 노는 줄 알고)
멈추기 쉬운데, 그 제출은 워커를 다시 켤 때까지 `running`에 갇힌다. 게다가 **DRFC 평가 스택은
워커와 별개로 계속 돌아** 다음 평가와 뒤엉킨다. 멈추기 전에 `docker stack ls`로
`deepracer-eval-0`이 없는지 확인한다.

> 다시 켜면 그 제출은 자동으로 대기열에 돌아가 재평가된다(§8.7). 잃는 것은 시간뿐이다.

**⚠️ 등록 전에 수동으로 띄워둔 워커를 반드시 먼저 끈다.** 한 머신에서 워커가 2개 돌면
`WORKER_ID`가 호스트명이라 **둘의 ID가 같아지고**, 나중에 시작한 쪽의 `recover_stale_running`이
"내 worker_id로 잡힌 running"이라고 판단해 **먼저 돌던 워커가 처리 중인 제출을 큐로 되돌린 뒤
이중으로 평가한다.** 같은 DRFC 스택과 같은 S3 경로를 공유하므로 모델과 영상도 섞인다.

```bash
pgrep -af -- '-m [w]orker\.run'
```

**딱 한 줄만** 나와야 정상이다.

> 워커가 시작할 때 `recover_stale_running`이 "평가중"에 멈춰 있던 제출을 대기열로 되돌린다.
> **자기 `worker_id`로 잡혀 있던 건은 시간과 무관하게 즉시** 회수되므로, 스팟이 회수됐다가
> 곧바로 복귀해도 제출이 갇히지 않는다. 자세한 내용은 §8.7.

### 8.7 스팟 회수 시 제출이 갇히던 문제 (2026-08-01 해결)

**이 절은 이미 고쳐진 내용이다.** 왜 이런 코드가 됐는지 알아야 나중에 되돌리지 않으므로 남겨둔다.

**있었던 문제.** `recover_stale_running`은 **워커가 시작할 때 딱 한 번만** 실행되는데
(`worker/run.py`의 `main()`), 예전에는 되돌리는 기준이 `started_at`이 35분 이상 지난 건뿐이었다.
그래서 이런 경우가 생겼다.

1. 평가 시작 5분 뒤 스팟이 회수되어 인스턴스가 중지된다
2. 10분 뒤 AWS가 인스턴스를 다시 켜고, systemd가 워커를 시작한다
3. 그 제출의 `started_at`은 15분 전 → 35분 기준에 못 미쳐 **되돌려지지 않는다**
4. 이후 `recover_stale_running`은 다시 호출되지 않으므로 **영구히 `running`에 갇힌다**

그 팀은 "이전 제출의 결과가 아직 나오지 않았습니다"에 막혀 새 모델을 못 올린다.

**어떻게 고쳤나.** 되돌리는 대상을 두 갈래로 나눴다(`worker/run.py`의 `or_`).

| 대상 | 기준 | 이유 |
|---|---|---|
| **내 `worker_id`로 잡혀 있는 것** | **시간 무관, 즉시** | 이 함수는 시작할 때만 도니까, 내 이름표가 붙은 "평가중"이 남아 있다는 건 **이전 생애의 내가 그 평가를 못 끝내고 죽었다**는 뜻이다. 지금 프로세스는 그 작업을 이어받을 수 없으므로 시간을 볼 이유가 없다 |
| 다른 워커가 잡은 것 | 35분 (`EVAL_MAX_WAIT_SECONDS` 1800초 + 5분) | 그 워커가 **지금도 정상 처리 중일 수 있다.** 남의 상태는 추측할 수밖에 없으므로 시간 기준이 반드시 필요하다 |

그래서 위 시나리오의 3번에서 제출이 즉시 대기열로 돌아간다. 스팟이 회수됐다 복귀하면
systemd가 워커를 다시 띄우고(§8.6), 그 순간 갇혀 있던 제출이 자동으로 풀린다.

**아직 남아 있는 경우 — 워커가 아예 다시 뜨지 않을 때.** 복구는 워커가 시작하는 순간에만
일어나므로, 인스턴스가 계속 내려가 있으면 제출은 `running`에 남는다. 노트북을 예비 워커로 켜면
그건 **다른 `worker_id`**라 35분 기준이 적용된다. 급하면 서버 DB에서 직접 되돌린다.

```sql
SELECT id, worker_id, started_at FROM submissions WHERE status = 'running';
```

```sql
UPDATE submissions SET status='queued', worker_id=NULL, started_at=NULL WHERE id=<제출ID>;
```

> **워커는 이 서버에서 1개만 실행한다.** 평가 영상이 S3의 고정 경로에 덮어써지기 때문에
> 한 서버에서 2개를 돌리면 영상이 섞인다. 처리량이 부족하면 노트북을 두 번째 워커로 켜는 것이
> 가장 안전하다(다른 기기라 경로가 겹치지 않는다). 같은 서버에서 병렬로 돌리려면
> `DR_RUN_ID`와 `DR_LOCAL_S3_MODEL_PREFIX`를 분리한 별도 `run.env`가 필요하고,
> `worker/run.py`의 `WORKER_ID`가 호스트명 고정이라 한 줄 수정도 필요하다.

### 8.8 예비 워커 — 노트북 (2026-08-01 결정, ⛔ 2026-10-04 폐기)

> ⛔ **2026-10-04부터 노트북을 예비 워커로 쓰지 않는다**
> ([명세서](../specs/001-online-virtual-evaluation/worker-auto-start-stop.md) §6 Q2).
>
> **왜**: 자동 켜기(§8.13)는 "대기 제출이 있는데 **대상 EC2의 워커**가 살아 있지 않으면 켠다"로
> 판단한다. 다른 기기의 워커는 판단에 넣지 않는다. 그래서 노트북 워커가 살아 있어도 EC2는 켜지고,
> 두 워커가 같은 대기열을 나눠 집는다. 그렇게 되면 이 절이 지키려던 "두 서버의 평가 조건이 같아야
> 한다"를 매번 확인해야 하고, 노트북을 덮거나 끌 때 제출이 노트북 `worker_id`로 갇히는 문제(아래
> ⚠️)도 그대로 남는다. 반대로 노트북 워커까지 판단에 넣으면, 노트북이 켜져 있는 동안 EC2가 안 켜져
> 노트북 하나에 대회가 걸린다. 평가 서버를 **온디맨드 1대**로 바꿔 회수 걱정이 없어졌으므로(명세서
> §1.3) 예비 워커 자체가 필요 없어졌다.
>
> **대신 할 일**: 몰릴 것 같으면 관리자 페이지 "평가 서버 자동화"에서 스위치를 끄고 서버를 켜 둔다
> (명세서 Q6). EC2가 켜지지 않으면 디스코드 `⚠️ 평가 서버 켜기 실패`/`⚠️ 평가 서버가 켜지지 않음`
> 알림이 오고, 콘솔에서 직접 켜고 원인을 본다(§8.13).
>
> 아래 내용은 왜 그렇게 운영했는지 남기기 위한 **기록**이다. 따라 하지 않는다.

**평상시 구성은 EC2 워커 1개다.** 10팀 기준 하루 최악 30건인데 평가 1건이 약 8분이라
4시간이면 소화된다. 노트북은 **꺼두었다가 필요할 때만 켜는 예비 워커**로 둔다.

**켤 만한 상황**: 제출 마감일처럼 몰릴 때, EC2에 문제가 생겼을 때, 스팟 용량이 없어
인스턴스가 안 뜰 때.

**켜기 전 확인** — 두 서버의 조건이 같아야 기록이 비교 가능하다.

```bash
grep -E "DR_EVAL_MAX_RESETS|DR_EVAL_OFF_TRACK_PENALTY|DR_WORLD_NAME" ~/deepracer-for-cloud/run.env
```

```bash
cd /mnt/c/Users/<사용자>/spg_deepracer_leaderboard && git pull
```

**켜기** (노트북 WSL) — 기동 절차는 [operations.md](operations.md)를 따른다.

**끄기 전에는 반드시 평가 중이 아닌지 확인한다.**

```bash
docker stack ls
```

`deepracer-eval-0`이 없을 때만 워커를 종료한다.

```bash
pgrep -f -- '-m [w]orker\.run' | xargs -r kill
```

> ⚠️ **노트북을 그냥 덮거나 끄면 안 된다.** 평가 도중 노트북이 꺼지면 그 제출이 노트북의
> `worker_id`로 `running`에 갇힌다. EC2 워커는 다른 `worker_id`이므로 35분이 지나기 전에는
> 회수하지 않고, 노트북 워커를 다시 켜야 즉시 회수된다(§8.7의 수정 내용). 예비 워커를 내릴
> 때는 위 순서를 지킨다.

> ⚠️ **노트북을 예비 워커로 쓰는 동안에는 그 노트북에서 DRFC 학습을 돌리지 않는다.**
> 학습과 평가가 같은 MinIO 경로를 쓰기 때문에 서로의 모델을 덮어쓴다
> ([operations.md](operations.md) §1-2).

### 8.9 로그는 어디에 있나

**노트북에 있던 `/tmp/worker.log`가 EC2에는 없다.** 이 파일은 코드가 만든 것이 아니라 노트북에서
워커를 띄울 때 쓰던 실행 명령의 **출력 리다이렉트**로 생긴 것이다.

```bash
setsid nohup bash worker/run_worker.sh > /tmp/worker.log 2>&1 < /dev/null &
#                                       ^^^^^^^^^^^^^^^^^ 이 부분이 파일을 만들었다
```

EC2에서는 systemd가 워커를 띄우므로 그 리다이렉트가 없고, 표준 출력이 **systemd 저널**로 간다.
로깅 방식이 바뀐 것이 아니라 **로그가 흘러가는 목적지만 바뀌었다.**

| 로그 종류 | 노트북 | EC2 |
|---|---|---|
| 워커 실행 로그 | `/tmp/worker.log` | **`journalctl -u drfc-worker`** |
| 평가 시뮬레이션 로그 | `storage/eval_logs/{제출ID}.log` | **동일** (`~/spg-deepracer-leaderboard/storage/eval_logs/`) |

**평가 실패 원인을 추적할 때 진짜 중요한 것은 두 번째다.** 이건 파일로 그대로 남아 있고 EC2에서도
바뀌지 않았다. DRFC는 시뮬레이션 로그를 디스크에 남기지 않고 스택을 지우면 사라지므로,
`run_evaluation.sh`가 스택을 내리기 전에 받아 저장한다.

**자주 쓰는 journalctl 명령**

```bash
journalctl -u drfc-worker -f
```

```bash
journalctl -u drfc-worker -n 100 --no-pager
```

```bash
journalctl -u drfc-worker --since today --no-pager
```

오류만 추리거나, 재부팅 이전 기록을 보려면:

```bash
journalctl -u drfc-worker -p err --no-pager
```

```bash
journalctl -u drfc-worker -b -1 --no-pager
```

파일로 넘겨야 할 때(공유·첨부 등)는 뽑아내면 된다.

```bash
journalctl -u drfc-worker --since today > ~/worker-today.log
```

> **저널 방식이 오히려 낫다.** `/tmp/worker.log`는 재부팅하면 사라져서 정작 사고가 난 뒤에 확인할
> 수 없었다([operations.md](operations.md)에도 같은 한계가 기록되어 있다). 저널은 재부팅을 넘어
> 보존되고 자동으로 로테이션되며, `-b -1`로 **이전 부팅의 로그**까지 볼 수 있다. 스팟이 회수됐다
> 복귀했을 때 무슨 일이 있었는지 확인하려면 이 기능이 필요하다.
>
> 굳이 파일로도 남기고 싶다면 유닛에 `StandardOutput=append:/var/log/drfc-worker.log`를 추가할 수
> 있지만, 로테이션이 없어 파일이 무한정 커지므로 logrotate를 따로 설정해야 한다. 권장하지 않는다.

### 8.10 워커 서버에서 파일 가져오기 — 무엇이 여기에만 남는가

**먼저 알아야 할 것: 워커 서버는 백업 원본이 아니다.**

`scripts/backup.sh`가 만드는 백업(DB + `storage/`)의 원본은 **웹 서버(Lightsail)**다. 워커 서버가
아니다. 노트북에서 전부 돌리던 시절에는 웹·DB·워커가 한 디스크에 있어서 "워커가 만든 파일"과
"백업 대상"이 같은 폴더였지만, 지금은 갈라졌다. 워커가 만든 결과물은 **평가가 끝나는 즉시
웹 서버로 올라간다.**

| 워커가 만드는 것 | 어떻게 되나 | 백업에 들어가나 |
|---|---|---|
| 평가 영상 `evaluation.mp4` | `deliver_video()`가 웹으로 업로드 → 서버 `storage/videos/` | ✅ |
| metrics json | `deliver_metrics()`가 웹으로 업로드 → 서버 `storage/metrics/` | ✅ |
| 제출 모델 | 웹에서 **받아온** 것이라 원본은 서버에 있다. 워커 쪽은 `storage/work/`의 임시본 | ✅ (원본이 서버에) |
| **평가 시뮬레이션 로그** `storage/eval_logs/{제출ID}.log` | **업로드 경로가 없다. 워커에만 남는다** | ❌ |
| 워커 실행 로그 (journald) | EC2 안에만 있다 | ❌ |
| MinIO 버킷 데이터 | 다음 평가에 덮어써진다. 보관 가치 없음 | — |
| `run.env` / `system.env` | 설정 파일. AMI(§8.5)에 들어 있다 | (AMI로) |

> `deliver_metrics()`에 이유가 그대로 적혀 있다 — *"워커에만 두면 백업(서버 기준)에서 빠지므로
> http 모드에서는 서버로 올린다"* (`worker/transfer.py`). 영상·metrics는 이 문제를 이미 해결했고,
> **`eval_logs`만 아직 해결되지 않은 채 남아 있다.**

**결론: 워커에서 굳이 가져와야 하는 건 `eval_logs` 하나다.** 평가가 왜 실패했는지(오프트랙 위치,
리셋 횟수, 시뮬레이터 오류) 추적할 때 쓰는 유일한 기록이고, **스팟 인스턴스를 종료하면 함께
사라진다**(§9.3). 대회가 끝나 인스턴스를 없애기 전에는 반드시 한 번 받아둔다.

**받는 명령** (노트북 WSL에서 실행. `<사용자>`는 Windows 사용자명)

```bash
rsync -avz -e "ssh -i ~/.ssh/drfc-worker-key.pem" ubuntu@100.93.165.104:/home/ubuntu/spg-deepracer-leaderboard/storage/eval_logs/ /mnt/c/Users/<사용자>/drleader-backup/eval_logs/
```

`rsync`라서 **여러 번 돌려도 새로 생긴 것만 받는다.** 매번 전체를 다시 받지 않는다.

`rsync`가 없으면 `scp`로도 된다(매번 전부 다시 받는다).

```bash
scp -i ~/.ssh/drfc-worker-key.pem -r ubuntu@100.93.165.104:/home/ubuntu/spg-deepracer-leaderboard/storage/eval_logs /mnt/c/Users/<사용자>/drleader-backup/
```

**워커 실행 로그도 함께 남기려면** (사고 조사용. 저널은 인스턴스를 없애면 사라진다)

```bash
ssh -i ~/.ssh/drfc-worker-key.pem ubuntu@100.93.165.104 'journalctl -u drfc-worker --no-pager' > /mnt/c/Users/<사용자>/drleader-backup/worker-journal-$(date +%F).log
```

> **주소는 Tailscale IP(`100.93.165.104`)를 쓴다.** 스팟 인스턴스는 중지·재시작할 때마다 공인 IP가
> 바뀌지만 Tailscale IP는 그대로다. 공인 IP를 적어두면 다음에 반드시 실패한다.

**언제 돌리나**

| 시점 | 이유 |
|---|---|
| 대회 종료 후, 인스턴스를 종료하기 전 | **필수.** 종료하면 EBS와 함께 영구 삭제된다 |
| 평가 실패 문의가 들어왔을 때 | 해당 제출 ID의 로그를 봐야 원인을 답해줄 수 있다 |
| 스팟이 회수된 날 | 무슨 일이 있었는지 저널로만 확인할 수 있다 |

**자동화하지 않은 이유**: `eval_logs`는 순위에 영향이 없는 진단 자료다. 잃어도 대회는 굴러가므로
매일 도는 백업에 넣어 실패 지점을 하나 더 만들 이유가 없다. 대신 **인스턴스를 없애는 절차(§9.3,
§10)에 이 명령을 넣어뒀다.** 진짜 잃을 수 있는 순간은 그때뿐이다.

### 8.11 인스턴스가 저절로 중지됐을 때 — 원인 확인 (2026-09-10 추가)

> 스팟 인스턴스가 로그 한 줄 없이 중지된 일이 있어 정리한 절차다. **조사하는 동안에는 아무것도
> 고치지 않는다** — 아래 ①의 두 행동은 되돌릴 수 없다.

**로그가 없는 게 아니라 못 읽는 것이다.** 워커 저널은 EBS 디스크에 그대로 남아 있지만 인스턴스가
꺼져 있어 들어가 볼 수 없다. 그래서 **원인은 인스턴스 바깥(AWS가 남긴 기록)에서 먼저 찾고**,
인스턴스가 다시 뜬 뒤에 안쪽 저널로 시각을 맞춰본다.

#### ① 하지 말 것 — 둘 다 되돌릴 수 없다

| 하지 말 것 | 이유 (AWS 문서) |
|---|---|
| **스팟 요청 취소** | 요청을 취소하면 **중지 상태의 인스턴스가 종료(terminate)된다.** 디스크와 `eval_logs`(§8.10)가 함께 사라진다 |
| **루트 볼륨 분리** ("디스크만 떼서 로그를 보자") | 분리된 채로 EC2가 재시작을 시도하면 **시작에 실패하고 인스턴스를 종료한다** |

#### ② 원인 코드 두 개를 본다 (콘솔)

**가) 인스턴스의 상태 전환 사유**

> EC2 → 인스턴스 → `drfc-worker` 선택 → 아래 첫 번째 탭(세부 정보) → **State transition reason**(상태 전환 사유)

콘솔 언어에 따라 표기가 조금 다르다. `Server.…` 또는 `Client.…`로 시작하는 코드가 적힌 칸을 찾는다.

**나) 스팟 요청의 상태**

> EC2 → 왼쪽 메뉴 **스팟 요청** → 해당 요청 선택 → **상태(Status)**

#### ③ 두 코드로 판정한다

| 상태 전환 사유 | 스팟 요청 상태 | 무슨 일인가 | 다음 |
|---|---|---|---|
| `Server.SpotInstanceShutdown` | `instance-stopped-no-capacity` | **AWS가 용량이 필요해 회수했다.** 가장 흔한 경우 | ④ |
| `Server.SpotInstanceShutdown` | `instance-stopped-by-price` | 스팟 가격이 최대 가격을 넘어 회수. §1.1대로 최대 가격을 비워뒀다면 드물다 | ④ |
| `Client.UserInitiatedShutdown` | `instance-stopped-by-user` | **누군가 콘솔·API로 중지했다** | ⑤에서 누가 했는지 확인. 자동으로 다시 켜지지 않는다 — "인스턴스 시작"을 눌러야 한다(§9.2) |
| `Client.InstanceInitiatedShutdown` | `instance-stopped-by-user` | **서버 안에서 `shutdown`·`poweroff`가 실행됐다** | "인스턴스 시작" 후 ⑥에서 누가 명령을 냈는지 확인 |
| `Server.ScheduledStop` | — | AWS 하드웨어 교체에 따른 예약 중지 | AWS Health 대시보드에 예정 이벤트가 있었는지 본다 |

> **스팟 요청 상태만 보면 틀린다.** AWS는 "콘솔에서 누가 중지함"과 "서버 안에서 shutdown 명령이
> 실행됨"을 **`instance-stopped-by-user` 한 코드로 묶는다.** 두 경우는 조사할 곳이 전혀 다르므로
> (CloudTrail vs 서버 저널) 인스턴스의 상태 전환 사유를 함께 봐야 갈린다.

#### ④ AWS가 회수한 경우 — 기다리는 것 말고 할 수 있는 게 없다

| 사실 (AWS 문서) | 운영상 의미 |
|---|---|
| **중단된 스팟 인스턴스는 EC2만 다시 켤 수 있다** | "인스턴스 시작"을 눌러도 `You can't start the Spot Instance '...' because there is no available Spot capacity.`로 거절된다(2026-09-10 실제로 겪음). 기다리거나 §8.12로 옮긴다 |
| **같은 가용 영역·같은 인스턴스 유형**에 용량이 생기면 자동으로 켠다 | 켜지면 systemd가 워커를 띄우고, 평가 중이던 제출은 자동으로 대기열로 돌아간다(§8.6, §8.7) |
| 중지 상태에서 **인스턴스 유형은 바꿀 수 없다** | "m7i 용량이 없으니 m6i로 바꾸자"가 안 된다 |
| 중지 중에는 **EBS 요금만** 나간다 | 기다리는 비용은 하루 약 430원(§9.1) |

**언제 돌아올지는 알 수 없다.** 대회 중이라 큐가 쌓이면 기다리지 말고 노트북 예비 워커를 켠다(§8.8).
(2026-10-04부터 노트북 예비 워커는 쓰지 않는다. 지금은 평가 서버가 온디맨드라 회수 자체가 없다.)
이때 평가 도중 멈춘 제출은 **EC2의 `worker_id`로 `running`에 갇혀 있다.** 노트북 워커는 다른
`worker_id`라 35분이 지나야 풀어주므로, 급하면 §8.7의 SQL로 직접 되돌린다.

**기다릴 수 없으면** 디스크를 AMI로 떠서 온디맨드(또는 다른 유형의 스팟)로 옮긴다 — §8.12.

#### ⑤ 누가 중지했는지 — CloudTrail

> CloudTrail → **이벤트 기록(Event history)** → 조회 속성 **리소스 이름** = 인스턴스 ID(`i-…`)

| 보이는 것 | 뜻 |
|---|---|
| `StopInstances` | 사람이나 자동화가 API로 중지했다. **사용자 이름** 칸에 누구인지 나온다 |
| `StopInstances`가 없다 | API로 끈 게 아니다 — AWS 회수이거나 서버 안에서 종료한 것(③으로 갈린다) |

이벤트 기록은 별도 설정 없이 **최근 90일**치를 보여준다.

> **`BidEvictedEvent`로 찾으라는 글이 많은데, 이 판정에 쓰지 않는다.** AWS 문서상 그 이벤트는
> 스팟이 **종료(terminate)** 됐을 때의 기록이다. 이 서버의 중단 동작은 **중지**(§1.1)라서 찍힌다고
> 기대할 근거가 없다. 회수 여부는 ③의 `Server.SpotInstanceShutdown`으로 판정한다.

#### ⑥ 인스턴스가 다시 뜬 뒤 — 서버 안쪽 기록

**AWS 기록과 서버 저널의 시각은 UTC다.** 한국 시각은 +9시간.

이전 부팅의 저널이 남아 있는지부터 본다. 목록에 `-1`이 있어야 한다.

```bash
journalctl --list-boots --no-pager | tail -3
```

꺼지기 직전의 마지막 기록. 종료 절차가 시작된 시각을 ③·⑤의 시각과 맞춰본다.

```bash
journalctl -b -1 -n 60 --no-pager
```

**누가 껐는지** 단서를 추린다. `sudo`로 실행된 종료 명령이 보이면 서버 안에서 끈 것이고,
`Power key`가 보이면 보통 바깥(AWS·콘솔)에서 종료 신호를 보낸 것이다.

```bash
journalctl -b -1 --no-pager | grep -iE "COMMAND=.*(shutdown|poweroff|halt)|power key"
```

그 시각에 워커가 평가 중이었는지는 §8.9의 명령으로 본다.

```bash
journalctl -u drfc-worker -b -1 -n 50 --no-pager
```

**꺼진 직후라면 서버에 들어가지 않고도** 콘솔 출력을 볼 수 있다.

> 인스턴스 선택 → **작업** → **모니터링 및 문제 해결** → **시스템 로그 가져오기**

AWS는 이 출력을 **마지막 출력 뒤 최소 1시간, 최근 64KB만** 보관한다고 보장한다. 몇 시간이 지났으면
기대하지 않는다.

#### (선택) 명령으로 한 번에 보기 — AWS CloudShell

운영자 PC에는 AWS CLI가 없고, 평가 서버에는 자격증명을 두지 않는다(§7.5). 대신 AWS 콘솔 위쪽의
**CloudShell** 아이콘을 누르면 로그인한 계정 권한으로 CLI가 바로 열린다(서울 리전 지원).

```bash
aws ec2 describe-instances --region ap-northeast-2 --filters Name=tag:Name,Values=drfc-worker --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,Code:StateReason.Code,Reason:StateTransitionReason,AZ:Placement.AvailabilityZone}' --output table
```

아래 명령들의 `<인스턴스ID>`에 위에서 나온 `ID`를 넣는다.

```bash
aws ec2 describe-spot-instance-requests --region ap-northeast-2 --filters Name=instance-id,Values=<인스턴스ID> --query 'SpotInstanceRequests[].{State:State,Status:Status.Code,Updated:Status.UpdateTime}' --output table
```

```bash
aws cloudtrail lookup-events --region ap-northeast-2 --lookup-attributes AttributeKey=ResourceName,AttributeValue=<인스턴스ID> --max-results 20 --query 'Events[].{Time:EventTime,Event:EventName,User:Username}' --output table
```

```bash
aws ec2 get-console-output --region ap-northeast-2 --instance-id <인스턴스ID> --output text | tail -40
```

> 전부 **조회** 명령이다. CloudShell에서 `cancel-spot-instance-requests`, `terminate-instances`,
> `stop-instances`는 치지 않는다(①).

#### (선택) 다음부터는 메일이 오게 하기

지금 구성은 회수돼도 아무도 모른다. EventBridge 규칙 두 개를 SNS 주제(이메일 구독)로 보내면 된다.

| 규칙 | 이벤트 패턴 | 언제 오나 |
|---|---|---|
| 회수 예고 | `{"source": ["aws.ec2"], "detail-type": ["EC2 Spot Instance Interruption Warning"]}` | 회수 **2분 전** |
| 중지 발생 | `{"source": ["aws.ec2"], "detail-type": ["EC2 Instance State-change Notification"], "detail": {"state": ["stopped"]}}` | 원인과 무관하게 **중지되는 순간** |

> **첫 번째만 걸면 부족하다.** AWS는 회수 예고를 "best effort"로만 보낸다고 명시하고, 사람이 끈 경우나
> 서버 안에서 종료한 경우에는 애초에 오지 않는다. 두 번째 규칙이 "원인 불문 꺼졌다"를 잡는다.

### 8.12 스팟 용량이 없을 때 — 온디맨드로 옮기기 (2026-09-10 추가)

> 스팟이 회수된 뒤 "인스턴스 시작"을 누르자
> `You can't start the Spot Instance '...' because there is no available Spot capacity.`가 떴고,
> 용량이 언제 돌아올지 알 수 없었다. **스팟 인스턴스를 온디맨드로 "변경"하는 기능은 없다.**
> 지금 디스크를 AMI로 떠서 온디맨드 인스턴스로 새로 띄운다.

**요금이 3배 넘게 오른다.** 급한 기간에만 쓰고, 스팟 용량이 돌아오면 같은 방법으로 되돌아가는
것을 전제로 한다.

| 구매 옵션 | 시간당 | 한 달 24시간 (EBS 1.3만원 별도) |
|---|---|---|
| 스팟 (§1.1) | $0.0699 | 약 7.2만원 |
| 온디맨드 | **약 $0.25 (추정)** | **약 26만원** |

> 온디맨드 요금은 §1.1의 "스팟 = 온디맨드의 약 28%"에서 역산한 값이다. **정확한 값은 3번의 인스턴스
> 유형 선택 화면에 표시되는 온디맨드 요금으로 확인한다.**

#### 왜 순서가 중요한가 — 옛 스팟이 되살아나면 두 대가 충돌한다

- 스팟 요청이 **영구**라서, 요청이 살아 있으면 용량이 돌아오는 순간 EC2가 옛 인스턴스를 **자동으로 켠다**(§8.11 ④).
- AMI로 복제한 새 서버에는 **Tailscale 신원 파일(`/var/lib/tailscale`)까지 똑같이** 들어 있다. 두 대가
  함께 켜지면 **같은 `100.93.165.104`를 두고 충돌해** 둘 다 DB에 제대로 붙지 못한다. Tailscale 관리
  콘솔에는 `Duplicate node key`로 표시된다.
- 그래서 **AMI가 완성된 것을 확인한 뒤, 새 서버를 띄우기 전에** 스팟 요청을 취소한다. 취소하면 옛
  인스턴스는 종료되지만, 방금 뜬 AMI가 그 디스크의 마지막 상태를 전부 담고 있어 잃는 것이 없다.

> "새 서버를 먼저 띄워 확인한 뒤 옛 것을 취소"가 더 안전해 보이지만, 그 사이 스팟 용량이 돌아오면
> 위 충돌이 난다. 옛 디스크는 AMI로 이미 보존됐으므로 옛 인스턴스를 남겨둘 이유가 없다. 만일을 위해
> **§8.5에서 떠둔 기존 AMI는 이 절차가 끝날 때까지 지우지 않는다.**

#### 0. 원인 코드부터 적어둔다 — 1분이면 된다

2번에서 옛 인스턴스가 종료되면 콘솔 목록에서 곧 사라져 **상태 전환 사유를 더는 볼 수 없다.**
§8.11 ②의 두 코드를 먼저 적어둔다. CloudTrail 기록(§8.11 ⑤)은 90일간 남으니 나중에 봐도 된다.

#### 1. 지금 디스크로 AMI를 뜬다

> EC2 → 인스턴스 → `drfc-worker`(중지됨) 선택 → **작업** → **이미지 및 템플릿** → **이미지 생성**

- 이미지 이름: `drfc-worker-YYYYMMDD`
- **이미 중지된 인스턴스라 재부팅 옵션은 영향이 없다.** §8.5가 재부팅을 요구하는 이유(쓰이다 만
  파일)는 종료 절차를 거쳐 꺼진 디스크에는 해당하지 않는다.
- EC2 → **AMI**에서 상태가 **사용 가능(available)** 이 될 때까지 기다린다. 몇 분에서 수십 분 걸린다.
- 이 AMI에는 `eval_logs`와 워커 저널까지 들어 있다. §9.3·§10은 "종료 전에 `eval_logs`를 받아둔다"를
  요구하지만 인스턴스가 꺼져 있어 할 수 없다. 대신 **새 서버에 그대로 따라오므로 잃지 않는다.**

#### 2. AMI가 "사용 가능"인 것을 눈으로 확인한 뒤에만 스팟 요청을 취소한다

> EC2 → 왼쪽 메뉴 **스팟 요청** → 해당 요청 선택 → **요청 취소**

- **옛 인스턴스가 종료된다. 되돌릴 수 없다.**
- 취소한 뒤 인스턴스 목록에서 옛 인스턴스가 **종료됨**으로 바뀌었는지 확인한다. 중지됨으로 남아
  있으면 직접 종료한다.

#### 3. AMI로 온디맨드 인스턴스를 띄운다

> EC2 → **AMI** → 1번 이미지 선택 → **AMI로 인스턴스 시작**

| 항목 | 값 | 이유 |
|---|---|---|
| 이름 | `drfc-worker` | §8.11의 CloudShell 명령이 이 이름으로 찾는다 |
| 인스턴스 유형 | **m7i.xlarge** | 평가 환경을 바꾸지 않는다. **여기서 온디맨드 시간당 요금을 확인한다** |
| 키 페어 | **§1.1에서 만든 기존 키 페어** | 새로 만들면 `~/.ssh/drfc-worker-key.pem`으로 못 들어간다 |
| 보안 그룹 | **기존 보안 그룹 선택** | SSH(22)·내 IP만(§1.1). 새로 만들면 규칙을 다시 잡아야 한다 |
| 스토리지 | 100 GiB gp3 (AMI 값 그대로) | 줄이지 않는다 |
| 고급 세부 정보 → 구매 옵션 | **스팟 인스턴스를 체크하지 않는다** | 체크하지 않으면 온디맨드다 |

용량 부족(`InsufficientInstanceCapacity`)으로 실패하면 **네트워크 설정 → 서브넷**에서 **다른 가용
영역**을 골라 다시 시작한다. 워커는 Tailscale로만 통신하므로 가용 영역이 바뀌어도 상관없다.

#### 4. 뜬 뒤 확인

§9.3 "되살리는 절차"의 표를 그대로 따른다. 이번 경우 특히 볼 것:

| 확인 | 명령 | 기대값 |
|---|---|---|
| Tailscale 주소 | `tailscale ip -4` | **`100.93.165.104` 그대로.** 옛 인스턴스가 종료돼 같은 신원으로 붙는다. 다르면 §3.4와 [handover.md](handover.md) §0을 고친다 |
| DB까지 닿는가 | §4.3 | 연결 성공 |
| 워커 | `sudo systemctl status drfc-worker` | `active (running)` |
| 워커 ID | `journalctl -u drfc-worker -n 20 --no-pager` | `워커 시작 (worker_id=ip-172-31-...)`가 **옛 서버와 다른 값**. 호스트명이 사설 IP에서 만들어지기 때문이다 |
| MinIO | `docker stack ls` | `s3` 있음 |

> Tailscale이 안 붙어 SSH가 안 되면 콘솔의 새 공인 IP로 들어간다. 보안 그룹이 "내 IP"만 허용하므로
> 그 사이 집·학교 IP가 바뀌었다면 보안 그룹의 SSH 규칙부터 고친다.

#### 5. 갇힌 제출을 확인한다

옛 서버가 평가 도중 회수됐다면 그 제출은 **옛 `worker_id`로 `running`에 남아 있다.** 새 서버는
호스트명이 달라 **다른 워커로 취급**되므로 §8.7의 "내 것은 즉시 회수"가 적용되지 않는다.

| 새 워커가 시작한 순간 그 제출의 `started_at`이 | 결과 |
|---|---|
| 35분 넘게 지났다 | 시작하면서 **자동으로 대기열로 돌아간다** |
| 35분이 안 됐다 | **다시는 자동으로 풀리지 않는다**(`recover_stale_running`은 워커가 시작할 때만 돈다). §8.7의 SQL로 직접 되돌린다 |

§8.7의 `SELECT`로 `running`이 남았는지 한 번 본다.

#### 6. 정리 (급하지 않다)

- 새 서버에서 **평가 1건이 성공한 뒤**(§8.4) 필요하면 AMI를 새로 뜨고, 옛 AMI는 §8.5의 두 단계
  (등록 취소 → 스냅샷 삭제)로 지운다.
- Tailscale 관리 콘솔에 `Duplicate node key`가 보이면 옛 인스턴스가 살아 있는 것이다. 종료됐는지
  다시 확인한다.

#### 다시 스팟으로 돌아갈 때

순서가 반대여도 된다. **온디맨드는 저절로 켜지지 않기 때문이다.**

1. 평가 중이 아닐 때(`docker stack ls`에 `deepracer-eval-0` 없음) 온디맨드 인스턴스를 **중지**한다
2. AMI를 뜬다
3. AMI로 시작하면서 §1.1의 스팟 설정(스팟·영구·중지)을 지정한다
4. 새 스팟 서버를 4번 표로 확인한 뒤 온디맨드 인스턴스를 **종료**한다 — 중지 상태로 있는 동안에는
   켜지지 않으므로 Tailscale 충돌이 없다

#### 대안 — 온디맨드 대신 다른 유형의 스팟

용량 부족은 **특정 가용 영역의 특정 유형**에 대한 것이다. 같은 4 vCPU / 16 GiB인 `m6i.xlarge`,
`m7a.xlarge`, `m6a.xlarge` 같은 유형은 스팟 여유가 있을 수 있어 요금을 지킬 수 있다. 절차는 위와
같고 3번에서 유형과 구매 옵션(§1.1의 스팟 설정)만 다르다.

- **이름에 `g`가 붙은 유형(`m7g` 등)은 ARM이라 이 AMI(x86)로 뜨지 않는다.**
- 하드웨어가 바뀌면 평가 1건 시간이 달라지므로 §8.4의 실측을 다시 한다.

### 8.13 자동 켜기·끄기 (2026-10-04 추가)

> 평가 서버를 **온디맨드 1대**로 두고, 평소에는 꺼 두었다가 제출이 들어오면 켜고, 30분 동안 할 일이
> 없으면 다시 끈다. 스팟은 회수가 잦았고(§8.11, 2026-10-01에는 디스크째 사라짐), 온디맨드를 24시간
> 켜 두면 세 배 넘게 비싸다(§9.4). 설계 근거는 명세서·계획서에 있다 —
> [명세서](../specs/001-online-virtual-evaluation/worker-auto-start-stop.md),
> [계획서](../specs/001-online-virtual-evaluation/worker-auto-start-stop-plan.md).

#### ① 전체 그림 — 켜기는 밖에서, 끄기는 안에서

```
 [웹 서버 - Lightsail]                          [평가 서버 - EC2 온디맨드]
   autopilot 컨테이너 (1분마다)                    drfc-autostop 타이머 (1분마다)
   ├ 대기 제출 있음 + 대상 워커 죽음                ├ 대기·평가 없음 + 평가 스택 없음 + SSH 없음
   │   → EC2 "시작" API 호출 (AWS 키 필요)          │   이 상태가 30분 이어지면
   └ 쌓인 알림을 디스코드로 보낸다                   └ DB에 "끕니다" 기록 → 워커 내림 → poweroff
              ▲                                              │
              └────────── DB의 이벤트 표 (Tailscale) ◀───────┘
```

| 역할 | 누가 | 왜 거기서 |
|---|---|---|
| **켜기** | 웹 서버의 `autopilot` 컨테이너 | 꺼져 있는 서버는 스스로 켤 수 없다. 늘 켜져 있는 웹 서버가 AWS API로 켠다. 그래서 **AWS 키는 웹 서버 `.env`에만** 있다(④) |
| **끄기** | 평가 서버 안의 `drfc-autostop` 타이머 | 서버 안에서 `poweroff`하면 AWS 키가 필요 없다. 그래서 평가 서버에는 지금처럼 **AWS 자격증명을 두지 않는다**(§7.5). 평가 중인지, SSH 접속이 있는지도 안에서만 정확히 안다 |
| **알림** | 웹 서버 `autopilot` 한 곳 | 평가 서버는 "알릴 일"을 DB에 적기만 한다. 디스코드 웹훅 주소가 웹 서버 한 곳에만 있다 |
| **스위치** | 관리자 페이지 → "평가 서버 자동화" | 기본값 **꺼짐**. 꺼짐 = "사람이 직접 관리" — 자동으로 켜지도 끄지도 않는다 |

웹 서버 쪽 설정(`.env` 키, `autopilot` 로그)은 [server-access.md](server-access.md), 디스코드 알림별
대응은 [operations.md](operations.md)를 본다. 이 절은 **평가 서버와 AWS 콘솔에서 하는 일**만 다룬다.

**설치 순서** (계획서 §6). 스위치 기본값이 꺼짐이라 마지막 단계 전까지는 아무것도 자동으로 움직이지 않는다.

1. ② "종료 시 동작"이 **중지**인지 확인 (AMI부터 떠 둔다 — §8.5)
2. ③ 워커 ID(호스트 이름) 확인
3. ④ IAM 사용자·정책 → 시뮬레이터 확인 → 액세스 키를 웹 서버 `.env`에
4. 웹 서버 배포([server-access.md](server-access.md) §5 — tar 전송 후 `up -d --build`)
5. ⑤ 평가 서버에 `git pull` → 타이머 등록 → `--dry-run` 확인
6. 관리자 페이지에서 스위치를 켠다

#### ② 전제 조건 — "종료 시 동작"이 **중지**여야 한다

자동 끄기는 서버 안에서 `poweroff`한다. 이때 인스턴스가 어떻게 되는지는 인스턴스 속성
**"종료 시 동작"(`InstanceInitiatedShutdownBehavior`)** 이 정한다.

| 종료 시 동작 | 서버 안에서 `poweroff`하면 |
|---|---|
| **중지(stop)** — 기본값 | 콘솔에서 "인스턴스 중지"를 누른 것과 같은 결과. 디스크·사설 IP(=워커 ID)가 남는다 |
| **종료(terminate)** | **인스턴스와 루트 볼륨이 통째로 사라진다.** DRFC, MinIO 이미지, `.env`, `eval_logs` 전부 |

> **2026-10-01 사고와 같은 결과다.** 그때는 스팟의 "중단 동작"이 기본값(종료)이라 회수되는 순간
> 디스크째 사라졌다. 장치는 다르지만(스팟 중단 동작 ≠ 종료 시 동작) 잃는 것은 같다. 그래서 웹 서버의
> `autopilot`이 기동할 때와 하루 한 번 이 값을 읽어, `stop`이 아니면(또는 읽지 못하면)
> 디스코드로 `🚨 평가 서버 설정 위험`을 보낸다.

**확인하는 곳** (콘솔):

> EC2 → 인스턴스 → `drfc-worker` 선택 → **작업** → **인스턴스 설정** → **종료 시 동작 변경**

**`중지`가 선택돼 있으면 아무것도 바꾸지 않고 취소한다.**

> ⛔ **시험해 본다고 `종료`로 바꾸지 않는다. 한 번이라도.** 바꿔 둔 채 잊으면 다음 자동 끄기(최대
> 30분 뒤)에 서버가 디스크째 사라진다. "알림이 제대로 오는지" 보려고 바꿀 이유도 없다 — 알림 경로는
> 다른 이벤트(`평가 서버 자동 중지` 등)로 확인된다.

> 📌 §1.1 표의 **"스팟 중단 동작"과는 다른 설정이다.** 스팟 중단 동작은 AWS가 회수할 때, 종료 시
> 동작은 **서버 안에서 끌 때** 적용된다. 콘솔의 "인스턴스 중지" 버튼에는 둘 다 적용되지 않는다.

**2026-10-04 실측** — 온디맨드 평가 서버(`ip-172-31-61-59`)에서 평가가 없을 때 `sudo poweroff`를
실행했다. SSH가 `closed by remote host`로 끊기고 잠시 뒤 콘솔에 **중지됨**이 떴다(상태 전환 사유
`Client.InstanceInitiatedShutdown`, §8.11 ③). 다시 켜니 사람이 아무것도 하지 않아도 `drfc-worker`가
`active (running)`으로 살아났다. 새 인스턴스를 만들면 이 시험을 한 번 다시 한다(**AMI를 먼저 뜬 뒤**).

#### ③ 워커 ID 확인 — 호스트 이름이 `ip-172-31-…` 형식이어야 한다

`autopilot`은 "대상 EC2의 워커가 살아 있나"를 이렇게 알아낸다.

1. 설정된 인스턴스 ID로 EC2에 물어 **프라이빗 IP DNS 이름**(`ip-172-31-61-59.ap-northeast-2.compute.internal`)을 받는다
2. 첫 점 앞(`ip-172-31-61-59`)을 잘라 **대상 워커 ID**로 쓴다(`app/autopilot_logic.py`의 `worker_id_from_private_dns`)
3. DB에서 그 워커 ID의 하트비트를 본다

그런데 워커 ID는 **평가 서버의 호스트 이름**이다(`worker/run.py`의 `socket.gethostname()`). 둘이 같아야
워커를 알아본다. 인스턴스를 만들 때 **호스트 이름 유형을 "리소스 이름"** 으로 고르면 호스트 이름이
`i-0abc…` 형식이 되어 둘이 어긋난다. 그러면 워커가 멀쩡히 돌아도 `autopilot`은 "대상 워커가 죽었다"로
보고, 켜진 서버에 계속 `⚠️ 평가 서버가 켜지지 않음`을 보낸다.

**확인** — 두 값의 앞부분이 같아야 한다.

| 어디서 | 방법 | 예 |
|---|---|---|
| 평가 서버 | `hostname` | `ip-172-31-61-59` |
| 콘솔 | EC2 → 인스턴스 → `drfc-worker` → 세부 정보 → **프라이빗 IP DNS 이름(IPv4만 해당)** | `ip-172-31-61-59.ap-northeast-2.compute.internal` |

`hostname`이 `i-…`로 나오면 같은 세부 정보 탭의 **호스트 이름 유형**이 "리소스 이름"으로 되어 있는
것이다. 인스턴스를 중지한 뒤 **작업 → 인스턴스 설정 → 리소스 기반 이름 지정 옵션 변경**에서
"IP 이름"으로 바꾼다(콘솔 버전에 따라 메뉴 이름이 조금 다를 수 있다). 워커 ID가 바뀌므로
평가 중이 아닐 때 한다.

> 관리자 페이지 "평가 서버 자동화" 화면에 **대상 워커 ID**가 보인다. 그 값과 `journalctl -u drfc-worker`의
> `워커 시작 (worker_id=…)` 값이 같은지 한 번 보면 된다.

#### ④ IAM — "그 인스턴스를 켜는 것"만 할 수 있는 사용자

Lightsail에는 IAM 역할을 붙일 수 없어서 **IAM 사용자의 액세스 키**를 웹 서버 `.env`에 둔다. 키가
새더라도 할 수 있는 최악의 일이 **"그 서버를 켜서 요금이 나가는 것"** 에 그치도록 권한을 좁힌다.
끄기·만들기·지우기 권한과 다른 서비스 권한은 주지 않는다.

> ⛔ **root 계정의 액세스 키는 절대 만들지도 쓰지도 않는다.** root 키가 새면 계정 전체를 잃는다.
> 2026-10-04 CloudTrail을 확인하다 콘솔 작업을 root로 하고 있던 것이 드러났다. 콘솔 작업도 가능하면
> 별도 IAM 사용자로 옮기는 것이 좋다.

**가) 사용자 만들기** (2026-10-04 생성됨)

> IAM → **사용자** → **사용자 생성**

| 항목 | 값 | 이유 |
|---|---|---|
| 사용자 이름 | `drleader-autopilot` | 무엇에 쓰는 키인지 이름으로 알 수 있게 |
| AWS Management Console에 대한 사용자 액세스 권한 제공 | **체크하지 않는다** | 프로그램만 쓰는 사용자다. 비밀번호가 없으면 콘솔로 들어올 길도 없다 |
| 권한 설정 | **아무 정책도 연결하지 않고** 다음 → 사용자 생성 | 권한은 나)에서 인라인 정책 하나로만 준다. `AmazonEC2FullAccess` 같은 관리형 정책은 붙이지 않는다 |

**나) 인라인 정책 붙이기**

> IAM → 사용자 → `drleader-autopilot` → **권한** 탭 → **권한 추가** → **인라인 정책 생성** → **JSON**

정책 이름: `autopilot-start-eval-server`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StartEvalServerOnly",
      "Effect": "Allow",
      "Action": "ec2:StartInstances",
      "Resource": "arn:aws:ec2:ap-northeast-2:<계정ID 12자리, 하이픈 없이>:instance/<인스턴스 ID>"
    },
    {
      "Sid": "CheckShutdownBehavior",
      "Effect": "Allow",
      "Action": "ec2:DescribeInstanceAttribute",
      "Resource": "arn:aws:ec2:ap-northeast-2:<계정ID 12자리, 하이픈 없이>:instance/<인스턴스 ID>"
    },
    {
      "Sid": "ReadInstanceState",
      "Effect": "Allow",
      "Action": "ec2:DescribeInstances",
      "Resource": "*"
    }
  ]
}
```

| 권한 | 대상 | 왜 |
|---|---|---|
| `ec2:StartInstances` | **대상 인스턴스 ARN 하나** | 유일한 쓰기 권한. 다른 인스턴스는 켤 수 없다 |
| `ec2:DescribeInstanceAttribute` | 대상 인스턴스 ARN 하나 | ②의 "종료 시 동작" 점검(`app/autopilot_aws.py`의 `shutdown_behavior`). 읽기 전용 |
| `ec2:DescribeInstances` | `*` | 상태·사설 DNS 이름 조회. **이 API는 대상을 좁힐 수 없다**(리소스 수준 권한 미지원). 읽기 전용이다 |

자리표시 채우는 법:

- **계정 ID**: 콘솔 오른쪽 위 계정 이름을 누르면 보인다. 화면에는 `1234-5678-9012`처럼 하이픈이 들어가
  있지만 **ARN에는 하이픈 없이 12자리**를 쓴다. 하이픈이 들어간 ARN은 실제 인스턴스와 일치하지 않아 허용되지 않는다
- **인스턴스 ID**: EC2 → 인스턴스 목록의 `i-…` 값. 웹 서버 `.env`의 `AUTOPILOT_INSTANCE_ID`와 같은 값이다
- **리전**: `ap-northeast-2` 그대로 둔다. **고쳐 쓰지 않는다**(아래 사고)

> **[왜 `DescribeInstanceAttribute`를 인스턴스 ARN으로 좁혔나]** 계획서 §5.3은 "좁혀지는지 확인하고,
> 안 되면 `*`"로 미뤄 두었다. 2026-10-04 AWS Service Authorization Reference의 EC2 항목을 확인했다
> ([사람이 읽는 표](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonec2.html),
> 같은 내용의 [기계 판독용 JSON](https://servicereference.us-east-1.amazonaws.com/v1/ec2/ec2.json)).
> `DescribeInstanceAttribute`는 리소스 유형으로 **`instance`** 를 받는다(ARN 형식
> `arn:${Partition}:ec2:${Region}:${Account}:instance/${InstanceId}`). 반면 `DescribeInstances`는
> 리소스 유형이 없어 `*`만 된다. "EC2의 Describe 계열은 전부 리소스 수준 권한을 지원하지 않는다"는
> 설명이 검색에 흔하지만 `DescribeInstanceAttribute`에는 맞지 않는다. 어느 쪽이든 **아래 시뮬레이터
> 표가 최종 판정이다.** 시뮬레이터에서 이 줄이 거부되면 이 문(`CheckShutdownBehavior`)의 `Resource`를
> `"*"`로 바꾼다(읽기 전용이라 위험이 거의 없다).

**다) 정책 시뮬레이터로 확인한다 — 키를 쓰기 전에 반드시**

> **2026-10-04 실제로 겪음 — 리전 오타 한 글자.** 처음 정책을 만들 때 ARN의 리전을 `ap-nortease-2`로
> 잘못 적었다. 정책은 오류 없이 저장됐다. 시뮬레이터를 돌려 보니 **`StartInstances`만 거부되고**
> `DescribeInstances`는 통과했다 — `DescribeInstances`는 `*`라서 ARN 오타의 영향을 받지 않기 때문이다.
> 이대로 배포했다면 `autopilot`은 상태 조회는 잘 되니 정상처럼 보이다가, 처음 켜야 할 순간에야
> `UnauthorizedOperation`으로 실패했을 것이다. **ARN 한 줄로 좁힌 권한은 한 글자만 틀려도 조용히
> 거부된다.** 그래서 저장한 뒤에는 반드시 시뮬레이터로 확인한다.

> https://policysim.aws.amazon.com → 왼쪽 **Users**에서 `drleader-autopilot` 선택 → 서비스
> **Amazon EC2** → 아래 표의 작업 선택 → 작업을 펼쳐 **리소스(Resource)** 칸에 ARN 입력 →
> **Run Simulation**

`<대상 ARN>` = 정책에 적은 ARN. `<다른 ARN>` = 대상 ARN에서 인스턴스 ID 끝 한 글자만 바꾼 것.

| 작업 | 리소스 | 기대 결과 | 이 줄이 확인하는 것 |
|---|---|---|---|
| `StartInstances` | `<대상 ARN>` | **allowed** | 켤 수 있다. **denied면 ARN 오타**(리전·계정 ID 하이픈·인스턴스 ID) |
| `StartInstances` | `<다른 ARN>` | **denied** | 다른 인스턴스는 못 켠다 |
| `DescribeInstanceAttribute` | `<대상 ARN>` | **allowed** | "종료 시 동작" 점검이 된다 |
| `DescribeInstances` | `*` | **allowed** | 상태 조회가 된다 |
| `StopInstances` | `<대상 ARN>` | **denied** | 끄기 권한이 없다 |
| `TerminateInstances` | `<대상 ARN>` | **denied** | 지우기 권한이 없다 |
| `RunInstances` | `*` | **denied** | 새 서버를 만들 수 없다 |

일곱 줄이 모두 기대와 같아야 다음으로 간다.

> 배포한 뒤에도 한 번 더 확인된다. `autopilot`은 기동할 때 바로 `DescribeInstances`와
> `DescribeInstanceAttribute`를 부른다. 권한이 틀렸으면 `docker compose -f docker-compose.prod.yml logs autopilot`
> (웹 서버 `~/drleader`)에 오류가 남고, 디스코드에 `🚨 평가 서버 설정 위험`(확인 실패)이 온다.
> `StartInstances`는 실제로 켤 일이 생겨야 불리므로 **시뮬레이터 말고는 미리 확인할 방법이 없다.**

**라) 액세스 키 발급**

> IAM → 사용자 → `drleader-autopilot` → **보안 자격 증명** 탭 → **액세스 키 만들기**

| 단계 | 고를 것 | 이유 |
|---|---|---|
| 사용 사례 | **AWS 외부에서 실행되는 애플리케이션** | Lightsail은 IAM 역할을 못 붙이는 "AWS 밖" 취급이다. 콘솔이 다른 방법(역할 등)을 권하는 안내가 떠도 이 경우는 해당하지 않는다 |
| 설명 태그 | `lightsail drleader autopilot` 등 | 나중에 키가 여러 개일 때 어디 쓰는 키인지 알아본다 |

**비밀 액세스 키는 이 화면에서 단 한 번만 보인다.** 창을 닫으면 다시 볼 수 없다(잃어버리면 새로
발급하고 옛 키를 지운다).

- 웹 서버에 SSH로 접속해 **`~/drleader/.env`에 바로 붙여 넣는다.** 키 이름은 `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`. `AUTOPILOT_INSTANCE_ID`도 함께 넣는다(나머지 키와 반영 방법은
  [server-access.md](server-access.md)).
- **채팅·문서·메모장·스크린샷에 붙이지 않는다**(CLAUDE.md §4). `.csv 파일 다운로드`도 누르지 않는다.
  눌렀다면 `.env`에 옮긴 뒤 그 파일을 지운다.
- `.env`는 tar 전송에서 빠지므로(CLAUDE.md §2) 로컬 `.env`에 넣어 두어도 서버에 가지 않는다.
  **서버 `.env`를 직접 고친다.**
- 이 키는 **웹 서버에만** 둔다. 평가 서버에는 넣지 않는다(§7.5) — 평가 서버는 키 없이 스스로 끈다.

**마) 키가 샜다고 의심되면 — 교체 순서**

새 키를 먼저 넣고 옛 키를 지운다. 반대로 하면 그 사이 자동 켜기가 실패한다.

1. IAM → `drleader-autopilot` → 보안 자격 증명 → 옛 키 **비활성화**(지우지 않는다 — 문제가 생기면 되살릴 수 있게)
2. **새 액세스 키 만들기**(라)
3. 웹 서버 `~/drleader/.env`의 `AWS_ACCESS_KEY_ID`·`AWS_SECRET_ACCESS_KEY`를 새 값으로 바꾼다
4. 웹 서버 `~/drleader`에서 `docker compose -f docker-compose.prod.yml up -d` — `.env`만 바뀐 경우다.
   코드도 바뀌었으면 `up -d --build`. **`restart`로는 `.env` 변경이 반영되지 않는다**
5. `logs autopilot`에 권한 오류가 없는지 본다
6. 옛 키를 **삭제**한다

> 키가 새도 할 수 있는 일은 "이 서버를 켜기"뿐이다(다의 표). 그래도 요금이 나가므로 의심되면 바로 바꾼다.
> CloudTrail 이벤트 기록(§8.11 ⑤)에서 **사용자 이름** = `drleader-autopilot`으로 걸러 보면 그 키로
> 무엇이 불렸는지 보인다.

#### ⑤ 평가 서버에 자동 끄기 타이머 등록

**코드 받기** — 평가 서버는 `git pull`이다(웹 서버의 tar 방식과 다르다 — CLAUDE.md §1).

```bash
cd ~/spg-deepracer-leaderboard && git pull
```

`.env`에 추가할 키는 없다. `worker/autostop.py`는 기존 `DATABASE_URL`만 쓴다(유휴 시간을 바꾸고
싶을 때만 선택으로 `AUTOSTOP_IDLE_MINUTES`).

**유닛 파일 두 개를 만든다.**

```bash
sudo nano /etc/systemd/system/drfc-autostop.service
```

```ini
[Unit]
Description=DeepRacer eval server auto-stop check (one shot)

[Service]
Type=oneshot
User=root
WorkingDirectory=/home/ubuntu/spg-deepracer-leaderboard
ExecStart=/home/ubuntu/spg-deepracer-leaderboard/.venv/bin/python -m worker.autostop
```

```bash
sudo nano /etc/systemd/system/drfc-autostop.timer
```

```ini
[Unit]
Description=Run drfc-autostop every minute

[Timer]
OnBootSec=5min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
```

각 항목이 왜 필요한지:

| 항목 | 이유 |
|---|---|
| `Type=oneshot` | 스크립트는 **한 번 판단하고 끝난다**. 상주하지 않으니 메모리를 쓰지 않고, 스크립트가 죽어도 다음 주기에 새로 뜬다 |
| `User=root` | `systemctl poweroff`, `systemctl stop drfc-worker`, `docker stack ls`에 필요하다. §8.6의 워커는 `ubuntu`로 돌지만 이 스크립트는 서버를 끄는 일이라 root여야 한다 |
| `WorkingDirectory` | `.env`를 상대경로로 읽는다(§8.6과 같은 이유) |
| `ExecStart`의 `.venv/bin/python` | 워커와 같은 파이썬 환경(§8.2 ②). `-m worker.autostop`이라 저장소 루트에서 실행돼야 `app`·`worker` 패키지를 찾는다 |
| `.service`에 `[Install]`이 없음 | 타이머가 부른다. 서비스를 직접 `enable`하지 않는다 |
| `OnBootSec=5min` | **부팅 5분 뒤부터** 확인을 시작한다. 스크립트가 잘못돼 켜지자마자 꺼 버려도, 그 5분 안에 SSH로 들어가 고칠 수 있다(AWS 개발자 글이 경고한 함정, 계획서 §2.3). 워커·도커가 다 뜨기 전에 "할 일 없음"으로 잘못 세는 것도 막는다 |
| `OnUnitActiveSec=1min` | 직전 실행 1분 뒤에 다시 실행. 30분 유휴 판단에는 1분 단위로 충분하다. systemd 타이머의 기본 오차(`AccuracySec` 1분) 때문에 실제 간격이 1~2분으로 흔들릴 수 있지만 30분 기준에는 영향이 없다 |
| `WantedBy=timers.target` | 부팅할 때 타이머가 자동으로 켜진다. 자동으로 켜진 뒤 다시 꺼지는 한 바퀴가 사람 없이 돌려면 필요하다 |

> 유휴 시작 시각 같은 상태는 `/run/drfc-autostop/`에 파일로 둔다. `/run`은 재부팅하면 비워지므로
> **켜질 때마다 유휴 시간을 0부터 다시 센다.** 의도한 동작이다 — 켜지자마자 "30분 지났다"로 꺼지지 않는다.

**등록하고 켠다.** 스위치가 꺼져 있는 동안에는 타이머가 돌아도 아무것도 끄지 않으므로 지금 켜도 안전하다.

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now drfc-autostop.timer
```

(`.service`가 아니라 **`.timer`** 를 enable한다.)

**확인**

```bash
systemctl list-timers drfc-autostop.timer
```

`NEXT`에 1분 안쪽 시각이, `LAST`에 직전 실행 시각이 보이면 돈다(부팅 직후 5분 동안은 `LAST`가 비어 있다).

판단만 보고 아무것도 끄지 않는 시험 실행 — **저장소 루트에서** 한다.

```bash
cd ~/spg-deepracer-leaderboard && sudo .venv/bin/python -m worker.autostop --dry-run
```

```
[dry-run] worker_id=ip-172-31-61-59 판단: 자동화 꺼짐 — 사람이 직접 관리
[dry-run] 끄지 않습니다
```

- 판단은 이 순서로 정해진다: 스위치 꺼짐 → `자동화 꺼짐 — 사람이 직접 관리`, 대기·평가 제출이나
  평가 스택이 있음 → `바쁨: …`, SSH 접속 있음 → `SSH 접속 중 — 끄지 않음 (N분째)`, 그 밖 →
  `유휴 N분 / 30분`. 스위치를 켜기 전이면 첫 번째가, 켠 뒤 지금처럼 접속해 있으면 세 번째가 정상이다.
- `--dry-run`은 상태 파일을 쓰지 않는다. 타이머가 쌓아 둔 유휴 시간을 읽어서 보여 주기만 한다.
- `worker_id`가 ③의 값과 같은지 본다.
- DB 오류가 나면 `.env`의 `DATABASE_URL`과 §8.1(`nc -zv`)을 본다.

**로그**

```bash
journalctl -u drfc-autostop -n 50 --no-pager
```

```bash
journalctl -u drfc-autostop -f
```

1분마다 `판단: …` 한 줄이 남는다. 꺼지기 직전 기록은 다시 켠 뒤 `journalctl -u drfc-autostop -b -1 -n 20 --no-pager`로
본다(§8.9의 `-b -1`). 끈 순서는 이렇다(`worker/autostop.py`의 `shut_down`).

1. DB에 `idle_stop` 기록 — 끄고 나면 알릴 수 없으니 **먼저** 남긴다. 실패하면 끄지 않는다
2. `systemctl stop drfc-worker` — 실패하면 끄지 않는다
3. 그 몇 초 사이 이 서버의 워커가 집은 `running` 제출을 `queued`로 되돌린다 — 실패하면 워커를 다시 올리고 끄지 않는다
4. `systemctl poweroff`

DB에 닿지 않거나 `docker stack ls`가 실패하면 **바쁜 것으로 보고 끄지 않는다.** 켜져 있는 손해는
요금이지만, 잘못 꺼서 생기는 손해는 평가 중단이기 때문이다.

> ⚠️ §8.6의 "평가 중에 `systemctl stop drfc-worker` 하지 않는다"는 사람에게 하는 경고다. 이 스크립트는
> 대기·평가가 0건이고 평가 스택이 없을 때만 워커를 내리고, 3단계로 갇힐 제출을 되돌린다.

#### ⑥ SSH로 접속해 있으면 꺼지지 않는다

작업 중에 서버가 꺼지면 안 되므로, **누군가 접속해 있으면 끄지 않는다.** 따로 표시할 필요가 없다.

| 규칙 | 내용 |
|---|---|
| 접속을 어떻게 아나 | 둘 중 하나라도 있으면 접속 중이다. **`who`** 에 로그인이 있다(보통의 `ssh`), 또는 **`ss`** 로 본 22번 포트 연결이 있다 |
| 왜 두 가지인가 | **VS Code 원격 접속, `scp`, `rsync`는 터미널을 열지 않아 `who`에 안 보인다.** 그래서 22번 포트 연결도 본다 |
| 접속을 끊으면 | **그때부터 30분을 새로 센다.** 접속 중에는 유휴 기록을 지운다 — 잠깐 끊었다 다시 붙는 사이에 꺼지지 않게 |
| 접속한 채 잊으면 | 할 일이 없는데 접속 때문에 **60분** 넘게 못 끄고 있으면 디스코드로 `SSH 접속 때문에 끄지 못하는 중`(`ssh_blocking`)이 **한 번** 온다. 작업이 끝났으면 접속을 끊는다 |

> **Tailscale로 접속해도 22번 포트라 똑같이 잡힌다.** 반대로 `ssh … 'journalctl …'`처럼 명령 하나만
> 실행하고 바로 끝나는 접속은 그 몇 초 동안만 접속으로 보인다.

#### ⑦ 자동 끄기를 잠시 멈추려면

**기본 방법은 관리자 페이지 스위치다.**

> 관리자 대시보드 → **평가 서버 자동화** → **자동화 끄기**

- 배포나 재시작 없이 **다음 판단(1분 안)부터** 반영된다. 끄면 자동으로 켜지도 끄지도 않는다.
- 마감 직전처럼 몰릴 때: 서버를 켜 두고 스위치를 끈다(명세서 Q6).
- 대회가 아닌 기간: 서버를 중지해 두고 스위치를 끈다.
- 스위치를 꺼 둔 채 대기 제출이 10분 넘게 처리되지 않으면 `⚠️ 자동화 꺼짐 · 대기 제출 있음`이 온다.
  끄고 서버 켜는 것을 잊은 경우를 잡는 알림이다.

**서버 안에서만 멈추는 방법** (스위치를 못 바꾸는 상황, 예: 웹 서버가 죽었을 때)

```bash
sudo systemctl stop drfc-autostop.timer
```

- `stop`은 **이번 부팅 동안만** 멈춘다. 재부팅하면 `enable` 상태라 다시 돈다. 계속 멈추려면
  `sudo systemctl disable --now drfc-autostop.timer`.
- 이 방법은 **끄기만** 멈춘다. 웹 서버의 자동 켜기는 그대로다. 그래서 보통은 스위치를 쓴다.
- 다 쓰면 반드시 되돌린다: `sudo systemctl enable --now drfc-autostop.timer`. 잊으면 서버가 계속
  켜져 요금이 나간다.

#### ⑧ 자주 나는 문제

| 증상 | 원인과 해결 |
|---|---|
| 디스코드 `⚠️ 평가 서버 켜기 실패` | `StartInstances` 실패. `UnauthorizedOperation`이면 정책 ARN 오타(④ 다의 표를 다시 돌린다), `InsufficientInstanceCapacity`면 AWS 용량 부족. 콘솔에서 직접 "인스턴스 시작"을 누르고 웹 서버 `logs autopilot`으로 원인을 본다 |
| 서버는 켜졌는데 `⚠️ 평가 서버가 켜지지 않음` | 10분 동안 대상 워커 하트비트가 없다. `systemctl status drfc-worker`, `journalctl -u drfc-worker`, `tailscale status`, §8.1. 워커가 멀쩡하다면 ③의 워커 ID 불일치를 의심한다 |
| `🚨 평가 서버 설정 위험` | **즉시 스위치를 끈다.** ②의 "종료 시 동작"을 `중지`로 되돌린다. 값이 이미 `중지`인데 오면 확인 자체가 실패한 것이다 — 정책의 `DescribeInstanceAttribute`(④ 다)를 본다 |
| 할 일이 없는데 안 꺼진다 | `--dry-run`의 판단 이유를 본다. `SSH 접속 중`(⑥, VS Code 창이 열려 있는지), `자동화 꺼짐`(⑦), `바쁨: 평가 스택 실행 중`(`docker stack ls`에 `deepracer-eval-*`가 남았는지). `systemctl list-timers`에 타이머가 없으면 등록이 빠진 것이다 |
| 꺼진 뒤 켜지자마자 `running`에 갇힌 제출이 있다 | 끄기 3단계가 되돌린다. 남아 있으면 `journalctl -u drfc-autostop -b -1`로 그 주기 기록을 본다. 갇힌 제출은 워커가 다시 켜질 때 `recover_stale_running`이 회수한다(§8.7, 같은 워커 ID라 즉시) |

---

## 9. 비용 관리 — 언제 켜고 끄는가

### 9.1 요금이 나가는 기준

**SSH 접속 여부는 요금과 아무 관계가 없다.** 과금 기준은 인스턴스가 `running` 상태로 있는
시간이다. 접속해 있든 없든, 평가를 돌리든 놀고 있든 켜져 있으면 똑같이 나간다.

| 인스턴스 상태 | 인스턴스 요금 | EBS 요금 |
|---|---|---|
| **running** | 시간당 $0.0699 ≈ 100원 | 계속 |
| **stopped** (중지) | 없음 | 계속 (100GB 기준 하루 약 430원) |
| **terminated** (종료) | 없음 | 없음 (디스크가 삭제된다) |

또한 EC2는 AWS 데이터센터에 있는 별개의 컴퓨터이므로, **운영자의 노트북을 꺼도 워커 서버는 계속
돌아간다.** 워커가 systemd 서비스로 등록되어 있어 SSH 세션을 닫아도 죽지 않는다(§8.6).

### 9.2 안 쓰는 기간에는 중지한다

대회 준비 기간처럼 평가가 필요 없는 동안에는 인스턴스를 중지해 인스턴스 요금을 아낀다.
**SSH만 끊는 것은 아무 효과가 없다.**

> EC2 → 인스턴스 → `drfc-worker` 선택 → **인스턴스 상태** → **인스턴스 중지**

| 알아둘 것 | 내용 |
|---|---|
| 직접 중지한 경우 | AWS가 자동으로 다시 켜지 **않는다**. 스팟 요청이 `disabled`로 대기하고, 사람이 "인스턴스 시작"을 눌러야 뜬다 |
| AWS가 회수한 경우 | 용량이 생기면 **EC2가 자동으로 다시 켠다**(§1.1의 중단 동작 = 중지). 위와 혼동하지 않는다 |
| 재시작 후 | 퍼블릭 IP가 바뀐다. tailnet 주소는 그대로이므로 접속에는 지장이 없다 |

**⚠️ 대회 기간에는 절대 중지하지 않는다.** 웹은 Lightsail에서 계속 돌아 제출은 정상 접수되지만
평가가 멈춰 큐에만 쌓인다. 참가자 화면에는 "평가 서버가 재개된 뒤 순서대로 처리됩니다"가 뜬다.

> **2026-10-04부터: 자동화 스위치가 켜져 있으면 대회 기간에 서버가 꺼져 있는 것이 정상이다**(§8.13).
> 제출이 오면 웹 서버가 켜고, 30분 할 일이 없으면 스스로 중지된다. 위 경고는 **스위치가 꺼진 상태에서
> 사람이 직접 중지하는 경우**에 해당한다. 스위치가 켜져 있으면 "직접 중지한 경우 자동으로 다시 켜지지
> 않는다"(위 표)도 달라진다 — 대기 제출이 있으면 웹 서버가 다시 켠다.

### 9.3 긴 휴지기에는 인스턴스를 아예 없앤다

**중지만으로는 EBS 요금(월 1.3만원)이 계속 나간다.** 대회가 끝나고 다음 대회까지 몇 달씩 비는
기간에는, 인스턴스를 **종료하고 AMI만 남기는 것**이 훨씬 싸다. 스냅샷은 실제 사용 용량만
과금되므로 월 2~3천원 수준이다.

| 기간 유형 | 조치 | 월 비용 (대략) |
|---|---|---|
| **대회 중** | running 24/7 | **8.5만원** |
| **짧은 휴지기** (며칠~몇 주) | 인스턴스 중지 | **1.3만원** (EBS만) |
| **긴 휴지기** (몇 달~다음 대회) | **종료 + AMI만 보관** | **2~3천원** |

세 번째가 다음 회장에게 넘길 기본 상태다. 1년을 놀려도 3만원이 안 된다.

**없애는 절차** — §10의 정리 절차를 따르되, **AMI와 그 스냅샷은 지우지 않는다.** 순서가 중요하다.

1. AMI가 최신 상태인지 확인한다(§8.5). 없거나 오래됐으면 **먼저 새로 뜬다**
2. **`eval_logs`를 노트북으로 받아둔다(§8.10).** AMI에는 들어 있지만 AMI를 되살리기 전에는 꺼내
   볼 수 없다. 평가 실패 원인을 나중에 확인하려면 지금 파일로 뽑아둬야 한다
3. EC2 → **스팟 요청** → 요청 **취소** (이걸 먼저 안 하면 인스턴스가 자동으로 다시 뜬다)
4. 인스턴스 **종료** — 루트 볼륨 100GiB가 함께 삭제되며 EBS 요금이 멈춘다
5. Tailscale 관리 콘솔에서 해당 기기를 삭제한다

**되살리는 절차**

> EC2 → **AMI** → 보관해둔 이미지 선택 → 작업 → **이 AMI로 인스턴스 시작**

인스턴스 유형·스토리지·스팟 설정은 §1.1의 값을 그대로 다시 지정한다(AMI에 담기지 않는다).
뜬 뒤에 확인할 것:

| 확인 | 명령 / 방법 |
|---|---|
| Tailscale 재연결 | `tailscale status` — 안 붙으면 `sudo tailscale up`으로 재인증하고 **키 만료를 다시 끈다**(§3.3) |
| tailnet 주소 | `tailscale ip -4` — 새 주소가 잡히면 이 문서 §3.4를 갱신한다 |
| 워커 자동 시작 | `sudo systemctl status drfc-worker` |
| MinIO | `docker stack ls`에 `s3` |
| 평가 조건 | `grep -E "MAX_RESETS|OFF_TRACK_PENALTY|WORLD_NAME" ~/deepracer-for-cloud/run.env` |
| 코드 최신 | `cd ~/spg-deepracer-leaderboard && git pull` |

`.env`와 `WORKER_TOKEN`은 AMI에 들어 있으므로 다시 설정할 필요가 없다. 다만 그 사이 서버 쪽
`WORKER_TOKEN`을 바꿨다면 맞춰줘야 한다.

자동 켜기·끄기(§8.13)를 쓰고 있었다면 새 인스턴스는 **인스턴스 ID와 사설 IP가 바뀐다.** 웹 서버
`.env`의 `AUTOPILOT_INSTANCE_ID`, IAM 정책 ARN(§8.13 ④ — 시뮬레이터 확인까지), "종료 시 동작"(§8.13 ②),
호스트 이름 유형(§8.13 ③)을 다시 맞춘다. `drfc-autostop.timer`는 타이머를 등록한 뒤 뜬 AMI라면 들어 있다(`systemctl list-timers drfc-autostop.timer`로 확인).

### 9.4 온디맨드 + 자동 켜기·끄기 (2026-10-04~)

§9.1~9.3은 스팟을 24시간 켜 두던 시절의 기준이다. 지금은 **온디맨드 1대를 필요할 때만 켠다**(§8.13).
켜져 있는 시간만 인스턴스 요금이 나가고, 꺼져 있는 동안은 EBS 요금만 나간다(§9.1 표).

2주 대회 기준 **인스턴스 요금만** 비교한다(명세서 §1.1·§1.2. m7i.xlarge 서울 온디맨드 시간당
$0.2478, 스팟 $0.0699. EBS 100GB 약 $4/2주(월 $9, §1.1)는 어느 방식이든 같다).

| 운영 방식 | 2주 인스턴스 요금 | 계산 | 단점 |
|---|---|---|---|
| 스팟 24시간 (§1.1) | 약 **$23** | $0.0699 × 24h × 14일 | 회수가 잦다. 2026-10-01에는 디스크째 사라졌다 |
| 온디맨드 24시간 (§8.12) | 약 **$83~89** | $0.2478 × 24h × 14~15일 | 회수는 없지만 세 배 넘게 비싸다 |
| **온디맨드 + 자동 켜기·끄기** | 약 **$28** (하루 8시간 켜짐 가정) | $0.2478 × 8h × 14일 | 꺼져 있을 때 들어온 첫 제출은 부팅 몇 분만큼 늦게 시작한다 |

- **실제 값: 대회 후 기록(S8)** — 대회가 끝나면 Billing 콘솔에서 이 인스턴스의 실제 인스턴스 요금과
  켜져 있던 시간을 여기에 적는다. 하루 8시간은 가정일 뿐이다. 성공 기준은 "24시간 온디맨드($83)보다
  확실히 낮다"이다(명세서 S8).
  ```
  실제 값: (대회 후 기록 — 기간 / 켜진 시간 합계 / 인스턴스 요금)
  ```
- 자동화 자체에는 **추가 요금이 없다**(2026-10-04 검토: 디스코드 웹훅, EC2 API 호출, IAM, 서버 안
  systemd 타이머 모두 무료). 탄력적 IP·CloudWatch 경보처럼 돈이 드는 방법은 일부러 쓰지 않았다.
- 켜져 있는 시간을 늘리는 것은 **SSH 접속을 열어 둔 채 잊는 것**(§8.13 ⑥)과 **스위치를 끄고 서버를
  켜 둔 채 잊는 것**(§8.13 ⑦)이다. 앞의 것은 60분 뒤 디스코드로 알려 준다. 뒤의 것은 알림이 없으니
  마감이 지나면 스위치를 다시 켠다.

---

## 10. 대회가 끝나면 — 정리 절차

**순서를 반드시 지킨다.** 반대로 하면 요금이 계속 나간다.

0. **평가 로그를 먼저 받아둔다** — §8.10. 인스턴스를 종료하면 `storage/eval_logs/`가 EBS와 함께
   영구 삭제된다. 이것만은 되돌릴 방법이 없다
1. **그 다음 스팟 요청을 취소한다** — EC2 → 왼쪽 메뉴 **스팟 요청** → 해당 요청 선택 → 취소
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
