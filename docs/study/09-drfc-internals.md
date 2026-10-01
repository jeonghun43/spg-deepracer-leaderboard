# 09. DRFC 내부 — 트랙·자동차·학습은 어떻게 만들어졌나

우리 워커는 `dr-start-evaluation` 한 줄을 호출한다([06-worker.md](06-worker.md) §9).
그 한 줄 아래에서 무슨 일이 벌어지는지가 이 문서의 주제다.

> **다루는 것**: DeepRacer 시뮬레이터(simapp)가 트랙을 만들고, 자동차를 만들고, 움직이고,
> 보상을 계산하고, 학습하고, 결과를 저장하는 방식.
> **다루지 않는 것**: 우리 플랫폼 코드(00~08번 문서), 참가자용 실습 절차(`docs/participants/`).

---

## 이 문서의 근거

DRFC는 두 개의 저장소가 맞물려 돌아간다. 이 문서의 모든 인용은 **태그를 고정해서** 확인했다.

| 저장소 | 역할 | 이 문서가 인용한 버전 |
|---|---|---|
| [deepracer-for-cloud](https://github.com/aws-deepracer-community/deepracer-for-cloud) | 오케스트레이션 — `dr-*` 명령, compose 파일, run.env | `master` |
| [deepracer-simapp](https://github.com/aws-deepracer-community/deepracer-simapp) | 시뮬레이터 본체 — ROS/Gazebo 패키지, `markov` 파이썬 코드, 트랙, 차량 모델 | **`v5.3.3`** |

`v5.3.3`은 ROS Noetic + Gazebo 11 + Python 3.8 기반이다. simapp의 `master`는 이후 **ROS 2 Jazzy +
Gazebo Harmonic**으로 옮겨갔지만, 아래에서 설명하는 구조(세 컨테이너 / 웨이포인트 판정 /
Redis 교환 / S3 체크포인트)는 그대로다. 파일 경로만 조금 다르다.

파일을 직접 열어 보려면:

```bash
git clone --depth 1 --branch v5.3.3 https://github.com/aws-deepracer-community/deepracer-simapp.git
```

---

## 0. 한 장 요약

```text
┌─────────────────── 같은 도커 이미지 하나 ───────────────────┐
│                                                             │
│  [robomaker]            [rl_coach]          [sagemaker]     │
│  ROS + Gazebo           부트스트랩          신경망 학습      │
│  시뮬레이션 실행    →   sagemaker 기동  →   PPO 갱신        │
│  경험을 만든다                              경험을 먹는다    │
│       │                                          ▲   │      │
│       │  경험 ⟨관측, 행동, 보상⟩                  │   │      │
│       └──────────► Redis (pub/sub) ──────────────┘   │      │
│                                                       │      │
│       ◄────────── S3(MinIO): 체크포인트 ◄─────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

**[쉬움]**
게임하는 사람(robomaker)과 복습하는 사람(sagemaker)이 따로 있다. 게임하는 사람은 계속
플레이해서 "이 상황에서 이렇게 했더니 몇 점" 기록을 남기고, 복습하는 사람은 그 기록만 보고
전략을 고친다. 고친 전략은 공유 폴더(S3)에 올려두고, 게임하는 사람이 그걸 다시 내려받아
다음 판에 쓴다.

**[전공]**
경험 수집(rollout)과 정책 갱신(training)을 **다른 프로세스로 분리**한 표준적인 분산 RL 구조다.
시뮬레이션은 CPU·GPU 사용 특성이 학습과 전혀 다르고, 시뮬레이터를 여러 개 띄워 병렬로 경험을
모으는 확장(DRFC의 `DR_WORKERS`)이 이 분리 없이는 불가능하다. 교환 매체는 두 가지로 나뉜다:
**경험은 Redis pub/sub**(휘발성·고빈도), **정책 가중치는 S3**(영속·저빈도).

---

## 1. 세 컨테이너, 하나의 이미지

**[무엇을]** `dr-start-training`을 치면 Docker Swarm 스택이 뜬다. 그 안에는 세 종류의 컨테이너가 있다.

[`docker/docker-compose-training.yml`](https://github.com/aws-deepracer-community/deepracer-for-cloud/blob/master/docker/docker-compose-training.yml):

```yaml
services:
  rl_coach:
    image: ${DR_SIMAPP_SOURCE}:${DR_SIMAPP_VERSION}
    command: ["source /root/sagemaker-venv/bin/activate && python3 /opt/ml/code/rl_coach/start.py"]
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock"   # ← 주목
  robomaker:
    image: ${DR_SIMAPP_SOURCE}:${DR_SIMAPP_VERSION}   # ← 같은 이미지
    environment:
      - WORLD_NAME=${DR_WORLD_NAME}
      - SAGEMAKER_SHARED_S3_BUCKET=${DR_LOCAL_S3_BUCKET}
      - S3_YAML_NAME=${DR_CURRENT_PARAMS_FILE}
```

**[왜 이미지가 하나인가]**
robomaker와 sagemaker는 하는 일이 완전히 다른데도 같은 이미지를 쓴다. 이유는 **둘이 같은
파이썬 패키지(`markov`)를 공유하기 때문**이다. 보상 파라미터를 만드는 코드와 그것을 학습에
쓰는 코드가 한 저장소 안에 있어야 서로 어긋나지 않는다. 이미지를 나누면 "시뮬레이터는 새
버전인데 학습기는 옛 버전"이라는 사고가 난다.

**[왜 rl_coach가 도커 소켓을 마운트하나]**
rl_coach는 학습을 하지 않는다. **sagemaker 컨테이너를 대신 띄워주는 부트스트랩**이다.
AWS 클라우드에서는 SageMaker 서비스가 학습 컨테이너를 띄워 주는데, 로컬에는 그 서비스가
없다. 그래서 rl_coach가 호스트의 도커 데몬에 직접 붙어(`/var/run/docker.sock`)
"SageMaker 역할을 할 컨테이너"를 만들어 낸다. `docker ps`에 `sagemaker` 컨테이너가
compose 파일에 없는데도 나타나는 이유가 이것이다.

> **[전공] 보안 관점**
> 도커 소켓 마운트는 사실상 **호스트 루트 권한**을 주는 것과 같다. 컨테이너 안에서
> `docker run -v /:/host`를 할 수 있기 때문이다. 개인 노트북/전용 평가 서버라서 감수하는
> 선택이고, 공유 서버에서 DRFC를 돌리면 안 되는 이유이기도 하다.
> 우리 평가 서버가 전용 EC2인 것과 [CLAUDE.md](../../CLAUDE.md)의 "평가 서버에 실 AWS
> 자격증명을 두지 않는다" 규칙이 여기서 만난다 — 소켓을 잡을 수 있으면 자격증명도 잡을 수 있다.

**[어떻게 컨테이너가 무엇을 할지 아는가]**
`command`가 아니라 **환경변수**로 정해진다. robomaker는 `WORLD_NAME`으로 어떤 트랙을 띄울지,
`S3_YAML_NAME`으로 어떤 설정 파일을 내려받을지 안다. 이 값들은 전부 `run.env`에서 왔다.

---

## 2. 트랙 — **보이는 트랙**과 **판정하는 트랙**은 다른 파일이다

이 문서에서 가장 중요한 절이다. 여기를 이해하면 보상함수의 절반이 풀린다.

### 2-1. 트랙 한 개를 이루는 파일들

`v5.3.3` 기준 `reInvent2019_track`이라는 트랙 하나는 이렇게 흩어져 있다.

| 파일 | 무엇 | 누가 읽나 |
|---|---|---|
| `bundle/worlds/reInvent2019_track.world` | Gazebo 월드 — 조명 5개 + 트랙 모델 include | Gazebo |
| `bundle/meshes/reInvent2019_track/*.dae` | 3D 메시(모양) + `textures/*.png`(무늬) | Gazebo 렌더러 |
| `bundle/meshes/.../reInvent2019_track_collisions.dae` | 충돌 판정용 별도 메시 | Gazebo 물리엔진 |
| **`bundle/routes/reInvent2019_track.npy`** | **웨이포인트 배열** | **`markov` 파이썬 코드** |

`.world` 파일은 놀랄 만큼 짧다 — 91줄이고 내용의 대부분이 조명이다.

```xml
<include>
  <uri>model://models/reInvent2019_track</uri>
</include>
```

**[쉬움]** 트랙은 두 벌로 존재한다. 하나는 **눈에 보이는 3D 모형**(카메라에 찍히라고 있는 것),
다른 하나는 **점 목록으로 된 지도**(코스를 벗어났는지 계산하라고 있는 것). 게임으로 치면
배경 그래픽과 충돌 판정 선이 따로 있는 것과 같다.

**[전공]** 렌더링 지오메트리와 판정 지오메트리의 분리다. 판정을 메시 기반으로 하면 매 스텝
삼각형 교차 검사를 해야 해서 비싸고, 무엇보다 "중앙선에서 몇 미터"나 "몇 번째 웨이포인트"
같은 **트랙 좌표계 질의**를 할 수 없다. 그래서 판정은 2D 폴리라인으로 따로 만든다.

### 2-2. `.npy` 안에는 무엇이 들어 있나

[`markov/track_geom/track_data.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/track_geom/track_data.py):

```python
waypoints = np.load(os.path.join(rospack.get_path(...), "routes",
                                 "{}.npy".format(rospy.get_param("WORLD_NAME"))))
poly_func = LinearRing if self.is_loop else LineString
self._center_line_forward_  = TrackLine(poly_func(waypoints[:, 0:2]))
self._inner_border_forward_ = TrackLine(poly_func(waypoints[:, 2:4]))
self._outer_border_forward_ = TrackLine(poly_func(waypoints[:, 4:6]))
...
self._road_poly_ = Polygon(self.outer_border, [self.inner_border])
```

**`.npy`는 N×6 실수 배열이다.** 한 행이 웨이포인트 하나이고, 6개 숫자는

```text
[ 중앙선 x, 중앙선 y, 안쪽 경계 x, 안쪽 경계 y, 바깥 경계 x, 바깥 경계 y ]
```

이 세 쌍이 각각 `shapely`의 `LinearRing`(닫힌 트랙) 또는 `LineString`(열린 트랙)이 되고,
바깥 경계에서 안쪽 경계를 구멍으로 뚫은 것이 **`road_poly` — 주행 가능 영역**이다.

**[왜 6열인가]** 보상함수가 받는 `waypoints`는 중앙선(0:2)뿐이다. 그런데 코스 이탈 판정에는
경계선이 필요하고, `track_width`는 그 두 경계 사이 거리로 매 스텝 계산된다. 즉 **참가자에게
보여주는 것보다 시뮬레이터가 아는 것이 더 많다.**

> **[전공] 왜 `LinearRing`인가**
> `LinearRing`은 자동으로 닫히고 `is_ccw`(반시계 여부)를 제공한다. `is_ccw`는
> `is_left_of_center` 계산에 그대로 쓰인다(§5-3). 또 `TrackLine.ndists`가
> `line.project(point, normalized=True)`로 각 웨이포인트의 **정규화 진행거리(0~1)** 를 미리
> 계산해 두기 때문에, 매 스텝 "지금 몇 %"를 O(1)에 가깝게 얻는다.

### 2-3. 코스 이탈은 어떻게 판정되나

```python
def points_on_track(self, points):
    return [self._road_poly_.contains(pnt) for pnt in points]
```

그리고 그 결과가 이렇게 쓰인다([`markov/agent_ctrl/utils.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/agent_ctrl/utils.py)):

```python
wheel_on_track = track_data.points_on_track(pos_dict[AgentPos.LINK_POINTS.value])
...
reward_params[RewardParam.WHEELS_ON_TRACK.value[0]] = all(wheel_on_track)
```

- 검사 대상은 **바퀴 4개의 위치**다 (`LINK_NAMES = ['racecar::left_rear_wheel', ...]`).
- `all_wheels_on_track`은 **넷 다 안쪽일 때만 True**.
- `is_offtrack`은 다른 값이다 — **넷 다 바깥으로 나갔을 때** 켜지는, 에피소드 종료 신호다.

**이 둘의 차이가 참가자가 가장 많이 틀리는 지점이다.** 바퀴 하나가 걸치면
`all_wheels_on_track`은 False가 되지만 `is_offtrack`은 여전히 False다. 즉
**`all_wheels_on_track == False`라고 해서 에피소드가 끝난 게 아니다.**

---

## 3. 자동차 — URDF로 조립된 물리 객체

**[무엇을]** 자동차는 그림이 아니라 **관절과 질량을 가진 물리 모델**이다.

[`bundle/urdf/deepracer/racecar.xacro`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/urdf/deepracer/racecar.xacro):

```xml
<xacro:wheel_transmission name="left_rear_wheel" />
<xacro:wheel_transmission name="right_rear_wheel" />
<xacro:wheel_transmission name="left_front_wheel" />
<xacro:wheel_transmission name="right_front_wheel" />
<xacro:steering_hinge_transmission name="left_steering_hinge" />
<xacro:steering_hinge_transmission name="right_steering_hinge" />
```

**바퀴 4개 + 조향 힌지 2개, 합계 6개의 구동 관절.** 그리고 카메라 링크가 붙는다
(`include_second_camera` 인자로 스테레오/단안이 갈린다. `deepracer_single_cam.urdf`,
`deepracer_stereo_cam_lidar.urdf` 등 조합별 파일이 따로 있다).

**[왜 xacro인가]** URDF는 XML이라 반복이 심하다. 바퀴 4개를 손으로 쓰면 같은 40줄이 네 번
들어간다. xacro는 **매크로와 인자를 가진 URDF 전처리기**다. `macros.xacro`에 바퀴 하나를
정의하고 이름만 바꿔 네 번 부른다. 우리 코드로 치면 Jinja2 템플릿의 `{% macro %}`와 같은 역할이다.

**[어떻게 조종되나]** 관절마다 ROS 컨트롤러가 붙는다
([`config/racecar_control.yaml`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/src/deepracer_simulation_environment/config/racecar_control.yaml)):

```yaml
left_rear_wheel_velocity_controller:
  type: effort_controllers/JointVelocityController
  joint: left_rear_wheel_joint
  pid: {p: 1.0, i: 0.0, d: 0.0, i_clamp: 0.0}
left_steering_hinge_position_controller:
  type: effort_controllers/JointPositionController
  pid: {p: 1.0, i: 0.0, d: 0.5}
```

**[전공] 여기서 읽을 것 두 가지.**

1. **`effort_controllers`** — 속도를 "설정"하는 게 아니라 **PID로 토크를 만들어 목표 속도에
   수렴시킨다.** 즉 명령한 속도와 실제 속도는 다르다. 관성과 마찰이 있고, 급가속은 즉시
   반영되지 않는다. 뒤에 나오는 "보상함수의 `speed`는 실측이 아니다"(§5-3)와 직결된다.
2. **앞바퀴 P=0.5, 뒷바퀴 P=1.0** — 앞뒤 게인이 다르다. 네 바퀴에 같은 속도 명령을 줘도
   응답이 다르게 나온다는 뜻이고, 코너에서의 거동에 영향을 준다.

---

## 4. 움직임 — 행동이 바퀴에 닿기까지

**[무엇을]** 신경망이 고른 행동은 정수 하나(이산 액션 공간의 인덱스)다. 그게 어떻게 바퀴를 돌리나.

[`markov/agent_ctrl/rollout_agent_ctrl.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/agent_ctrl/rollout_agent_ctrl.py):

```python
def send_action(self, action):
    json_action = self._model_metadata_.get_action_dict(action)
    steering_angle = float(json_action[ModelMetadataKeys.STEERING_ANGLE.value])
    steering_angle = max(min(const.MAX_ANGLE, steering_angle), const.MIN_ANGLE)
    steering_angle = steering_angle * math.pi / 180.0     # 도 → 라디안
    action_speed = self._update_speed(action)
    send_action(self._velocity_pub_dict_, self._steering_pub_dict_,
                steering_angle, action_speed)
```

그리고 그 `send_action`은([`agent_ctrl/utils.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/agent_ctrl/utils.py)):

```python
def send_action(velocity_pub_dict, steering_pub_dict, steering_angle, speed):
    for _, pub in velocity_pub_dict.items():
        pub.publish(speed)
    for _, pub in steering_pub_dict.items():
        pub.publish(steering_angle)
```

전체 사슬:

```text
신경망 출력: 행동 인덱스 3
   ↓  model_metadata.json 조회
{"steering_angle": 15.0, "speed": 2.0}
   ↓  도→라디안 변환, MIN/MAX_ANGLE로 클램프
   ↓  ROS 토픽에 Float64 발행 (바퀴 4 + 힌지 2 = 6개 토픽)
/racecar/left_rear_wheel_velocity_controller/command   ← 2.0
/racecar/left_steering_hinge_position_controller/command ← 0.2618
   ↓  ros_control + PID
Gazebo 물리엔진이 토크를 적용 → 차가 실제로 움직임
```

**[왜 6개 토픽에 같은 값을 뿌리나]** 차동 기어나 애커먼 조향 계산이 없다. 네 바퀴에 같은 각속도,
두 힌지에 같은 각도를 준다. **실제 자동차보다 단순한 모델**이고, 그래서 시뮬레이터가 가볍다.
Sim2Real 격차의 한 원인이기도 하다.

**[전공]** 여기서 `model_metadata.json`의 위상이 드러난다. 그것은 단순한 설정 파일이 아니라
**행동 인덱스 → 물리 명령의 사전(dictionary)** 이다. 그래서 학습된 `.pb`와 `model_metadata.json`은
**반드시 짝으로 움직여야 한다.** 액션 스페이스를 바꾸고 옛 체크포인트를 얹으면, 신경망이
"3번 행동"을 고를 때 완전히 다른 조향각이 나간다. 우리 서비스가 제출 아카이브에서
`model_metadata.json`을 필수로 보는 이유가 이것이다([06-worker.md](06-worker.md) §9-3).

---

## 5. 한 스텝의 생애

**[무엇을]** 15fps로 반복되는 한 사이클을 순서대로 따라간다.

```text
① 카메라가 이미지 한 장 발행  (160×120, 그레이스케일)
       ↓
② 신경망이 행동 인덱스를 출력
       ↓
③ send_action() → ROS 토픽 → Gazebo 물리 진행
       ↓
④ update_agent(): 차의 실제 위치를 Gazebo에서 조회
       ↓
⑤ set_reward_and_metrics(): 파라미터 딕셔너리를 채운다
       ↓
⑥ judge_action(): **사용자 보상함수 호출** → reward
       ↓
⑦ 종료 조건 판정 → done
       ↓
⑧ ⟨관측, 행동, 보상, 다음 관측⟩을 Redis로 발행
```

### 5-1. 관측 — 왜 160×120 그레이스케일인가

[`markov/environments/constants.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/environments/constants.py):

```python
# Dimensions of the input training image
TRAINING_IMAGE_SIZE = (160, 120)
...
# The number of steps to wait before checking if the car is stuck
# This number should correspond to the camera FPS, since it is pacing the step rate.
NUM_STEPS_TO_CHECK_STUCK = 15
```

**[쉬움]** 차는 흑백 사진 한 장만 본다. 그것도 아주 작은. 속도계도 없고 지도도 없다.
1초에 15번, 그 사진만 보고 핸들과 속도를 정한다.

**[전공]** 세 가지가 여기서 결정된다.

- **그레이스케일 + 저해상도** — 입력 차원을 줄여 CNN 학습을 가능한 규모로 만든다.
  대신 색 정보가 사라져, 색으로 구분되는 요소(예: 노란 중앙선 vs 흰 경계선)를 구분하지 못한다.
- **`NUM_STEPS_TO_CHECK_STUCK = 15`의 주석** — "카메라 FPS와 일치해야 한다. 카메라가 스텝
  속도를 결정하기 때문이다." 즉 **시뮬레이션은 카메라 프레임에 맞춰 진행된다.**
  스텝 15개 = 실제 시간 약 1초. 참가자가 쓰는 `progress / steps` 효율 지표의 단위가 여기서 나온다.
- **부분관측(POMDP)** — 이미지 한 장에는 속도가 담기지 않는다. 정지 사진으로는 빠른지 느린지
  알 수 없다. 그래서 정책은 원리적으로 자기 속도를 모른 채 행동을 고른다.
  (그래서 `speed`를 보상에 쓰는 것이 도움이 된다 — 보상 신호로 간접 학습된다.)

### 5-2. 보상함수 파라미터는 어떻게 채워지나

[`markov/agent_ctrl/constants.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/agent_ctrl/constants.py)의 `RewardParam` enum이 **파라미터의 단일 원천**이다.

```python
class RewardParam(Enum):
    WHEELS_ON_TRACK = ['all_wheels_on_track', True]
    X = ['x', 0.0]
    ...
    PROJECTION_DISTANCE = ['projection_distance', 0.0]   # ← AWS 공식 문서에 없다
    OBJECT_IN_CAMERA = ['object_in_camera', False]       # ← 이것도 없다
    OFFTRACK = ['is_offtrack', False]

    @classmethod
    def make_default_param(cls):
        return {key.value[0]: key.value[-1] for key in cls}
```

**발견 1 — 문서에 없는 파라미터가 있다.** `projection_distance`(중앙선에 투영한 진행 거리, 미터)와
`object_in_camera`는 [AWS 공식 파라미터 문서](https://docs.aws.amazon.com/deepracer/latest/developerguide/deepracer-reward-function-input.html)에
없지만 실제 딕셔너리에는 들어 있다. `progress`(%) 대신 **미터 단위 진행 거리**가 필요할 때 쓸 수 있다.
다만 공식 지원이 아니므로 AWS 콘솔 학습으로 옮길 때 깨질 수 있다.

**발견 2 — enum의 주석이 낡았다.** `PROG = ['progress', 0.0] # float: ... [0,1]`이라고 적혀 있지만,
실제 값은 백분율이다:

```python
def compute_current_prog(current_progress, prev_progress):
    current_progress = 100 * current_progress
    if current_progress <= 0:            # 역주행
        current_progress += 100
    if prev_progress > current_progress + 50.0:   # 결승선 통과(정방향)
        current_progress += 100.0
    if current_progress > prev_progress + 50.0:   # 결승선 통과(역방향)
        current_progress -= 100.0
    return min(current_progress, 100)
```

**[왜 ±50 보정이 있나]** 정규화 거리는 결승선에서 0.99 → 0.01로 **되감긴다.** 그대로 두면
진행률이 갑자기 98% 떨어져, `progress` 증가분을 보상으로 쓰는 함수가 그 순간 거대한 음수를 받는다.
"한 스텝에 50%가 변할 리 없다"는 상식을 코드로 넣어 되감김을 흡수한 것이다.

> **[전공] 이것은 우리 코드에도 같은 형태로 있다.**
> 순환하는 값을 다루는 코드는 어디서나 이 보정이 필요하다. 우리 워커의
> `summarize_progress`가 로그에서 최대 진행률을 뽑을 때 마지막 값이 아니라 **최댓값**을 쓰는 것도
> 같은 성질의 방어다([06-worker.md](06-worker.md) §9-4).

### 5-3. 반드시 알아야 할 세 가지 함정

[`markov/agent_ctrl/utils.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/agent_ctrl/utils.py)의 `set_reward_and_metrics`를 읽으면 나온다.

**① `speed`와 `steering_angle`은 실측값이 아니다.**

```python
step_metrics[StepMetrics.STEER.value] = \
    reward_params[RewardParam.STEER.value[0]] = \
    float(json_actions[ModelMetadataKeys.STEERING_ANGLE.value])
step_metrics[StepMetrics.THROTTLE.value] = \
    reward_params[RewardParam.SPEED.value[0]] = \
    float(json_actions[ModelMetadataKeys.SPEED.value])
```

**`json_actions`는 `model_metadata.json`에서 꺼낸 값이다.** 즉 보상함수가 받는 `speed`는
"차가 지금 실제로 내는 속도"가 아니라 **"방금 명령한 목표 속도"** 다. §3에서 본 PID 때문에
실제 속도는 이 값에 뒤늦게 수렴한다.

이것이 실전에 주는 결론: **`speed`에 보상을 준다는 것은 "빠르게 달린 것"이 아니라
"빠른 행동을 고른 것"에 점수를 주는 것이다.** 벽에 부딪혀 멈춰 있어도 최고 속도 행동을 고르면
만점을 받는다. 그래서 속도 보상은 반드시 `progress`나 위치 조건과 함께 써야 한다.

**② `track_width`는 매 스텝 다시 계산된다.**

```python
reward_params[RewardParam.TRACK_WIDTH.value[0]] = \
    nearest_pnts_dict[TrackNearPnts.NEAR_PNT_IN.value] \
    .distance(nearest_pnts_dict[TrackNearPnts.NEAR_PNT_OUT.value])
```

상수가 아니라 **현재 위치에서 가장 가까운 안쪽 점과 바깥 점 사이의 거리**다. 트랙 폭이
구간마다 다르면 이 값도 달라진다. `track_width * 0.5`를 "코스 절반"으로 쓰는 관용구가
성립하는 것도 이 계산 덕분이다.

**③ `heading`은 쿼터니언에서 온 도(degree) 값이다.**

```python
reward_params[RewardParam.HEADING.value[0]] = \
    Rotation.from_quat(model_orientation).as_euler('zyx')[0] * 180.0 / math.pi
```

`as_euler('zyx')[0]`는 yaw이고, 라디안을 도로 바꿔 −180~180 범위로 준다. 웨이포인트로 계산한
`math.atan2(dy, dx)`(라디안)와 비교하려면 **단위를 맞추고, 각도 차를 −180~180으로 정규화**해야 한다.
이 정규화를 빠뜨리면 359도 차이를 "거의 반대 방향"으로 오판한다.

### 5-4. 내 보상함수가 호출되는 정확한 지점

```python
def _judge_action_at_run_phase(self, episode_status, pause):
    try:
        reward = float(self._reward_(copy.deepcopy(self._reward_params_)))
    except Exception as ex:
        raise RewardFunctionError('Reward function exception {}'.format(ex))
    if math.isnan(reward) or math.isinf(reward):
        raise RewardFunctionError('{} returned as reward'.format(reward))
    if not (-1e5 <= reward <= 1e5):
        raise RewardFunctionError('Reward score out of range. Reward: {}, range: [-1e5, 1e5]'...)
```

네 가지 방어가 한 줄씩 들어 있다.

| 방어 | 왜 |
|---|---|
| `copy.deepcopy(params)` | 사용자가 딕셔너리를 수정해도 시뮬레이터 내부 상태가 오염되지 않는다 |
| `float(...)` | 문자열이나 `None`을 돌려주면 여기서 즉시 실패 — 나중에 이상하게 죽지 않는다 |
| `isnan / isinf` 검사 | NaN이 손실 함수까지 흘러가면 **신경망 전체가 NaN이 된다.** 학습 몇 시간이 조용히 증발하는 사고를 원천 차단 |
| `−1e5 ≤ reward ≤ 1e5` | 스케일 폭주 차단 |

**[쉬움]** 보상함수가 이상한 값을 돌려주면 학습이 조용히 망가지는 게 아니라 **그 자리에서
에러를 내고 멈춘다.** 그게 더 친절하다. 4시간 뒤에 "왜 안 되지?" 하는 것보다 낫다.

**[전공]** 이것이 **fail-fast**의 교과서적 사례다. 특히 NaN 검사가 중요하다. NaN은 전파되고
흡수되지 않는다. 한 번 가중치에 들어가면 이후 모든 출력이 NaN이 되며, 손실도 NaN이라
역전파가 무의미해진다. 발생 지점(사용자 함수)에서 잡는 것이 유일하게 싼 방법이다.
우리 코드에도 같은 사고방식이 있다 — `_looks_like_minio_raw_dump`로 **평가를 시작하기 전에**
아카이브가 쓸모없음을 판정하는 것([`worker/drfc.py`](../../worker/drfc.py))과 같은 종류다.

### 5-5. 에피소드는 언제 끝나나

`judge_action`이 `reset_rules_manager`의 상태를 모아 판정한다. 종료 사유(`EpisodeStatus`)는
`off_track`, `crashed`, `reversed`, `episode_complete`, `time_up`, `park` 등이다.
페널티도 여기 붙는다:

```python
self._penalties = {
    EpisodeStatus.OFF_TRACK.value: config_dict.get(ConfigParams.OFF_TRACK_PENALTY.value, 0.0),
    EpisodeStatus.CRASHED.value:   config_dict.get(ConfigParams.COLLISION_PENALTY.value, 0.0),
    ...
}
```

**중요:** 이 페널티는 **보상 점수가 아니라 "정지 시간(초)"** 이다. `_pause_duration`에 더해져,
차가 그만큼 멈춰 있다가 다시 출발한다. 랩타임에 손해를 주는 방식으로 벌을 준다.
**보상함수에서 코스아웃에 다시 벌점을 주는 것은 이중 처벌**이라는 조언의 근거가 이것이다.

---

## 6. 내 `reward_function.py`는 어떻게 컨테이너 안에서 실행되나

**[무엇을]** 참가자가 로컬에서 작성한 파이썬 파일이, 도커 컨테이너 안 시뮬레이터의
매 스텝 호출되는 함수가 되기까지.

[`markov/boto/s3/files/reward_function.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/boto/s3/files/reward_function.py):

```python
def __init__(self, bucket, s3_key, ...,
             local_path="./custom_files/agent/customer_reward_function.py", ...):
    # ./custom_files/agent/customer_reward_function.py
    #   → custom_files.agent.customer_reward_function
    self._import_path = local_path.replace(".py", "").replace("./", "").replace("/", ".")

def get_reward_function(self):
    if not self._reward_function:
        self._download()
        reward_function_module = __import__(self._import_path, fromlist=[None])
        self._reward_function = reward_function_module.reward_function
```

전체 경로:

```text
내 노트북: custom_files/reward_function.py
   ↓  dr-upload-custom-files
MinIO(S3): s3://bucket/custom_files/reward_function.py
   ↓  robomaker 컨테이너가 부팅하며 다운로드
컨테이너: ./custom_files/agent/customer_reward_function.py
   ↓  __import__ 로 모듈 적재, reward_function 심볼만 꺼냄
메모리: self._reward_ 로 보관, 매 스텝 호출
```

**[왜 여기가 중요한가]** 세 가지 실전 결론이 나온다.

1. **파일을 고쳐도 S3에 올리기 전엔 아무 일도 안 일어난다.** `dr-upload-custom-files`를
   빼먹고 재학습하면 옛 보상함수로 학습된다. 참가자가 가장 자주 겪는 "고쳤는데 안 바뀌어요"의 정체다.
2. **적재는 학습 시작 시 딱 한 번이다.** 학습 도중 파일을 바꿔도 반영되지 않는다.
3. **모듈 전역 상태가 에피소드를 넘어 유지된다.** `__import__`는 한 번만 일어나므로,
   보상함수 파일에 전역 변수를 두면 그 값은 에피소드가 바뀌어도 살아 있다. 의도적으로 쓰면
   "이전 스텝 기억"이 되고, 모르고 쓰면 재현 불가능한 버그가 된다.

---

## 7. 학습 — 두 워커와 Redis

### 7-1. 롤아웃 워커 (robomaker 쪽)

[`markov/rollout_worker.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/rollout_worker.py):

```python
graph_manager.data_store.wait_for_checkpoints()
graph_manager.data_store.wait_for_trainer_ready()
...
episode_steps_per_rollout = graph_manager.agent_params.algorithm.num_consecutive_playing_steps.num_steps
act_steps = int(episode_steps_per_rollout / num_workers)

while True:
    exit_if_trainer_done(checkpoint_dir, ...)
    graph_manager.act(act_steps, wait_for_full_episodes=...)
    ...
    while new_checkpoint < last_checkpoint + 1:
        exit_if_trainer_done(checkpoint_dir, ...)
        new_checkpoint = data_store.get_coach_checkpoint_number('agent')
    data_store.load_from_store(expected_checkpoint_number=last_checkpoint + 1)
    graph_manager.restore_checkpoint()
    last_checkpoint = new_checkpoint
```

**[쉬움]** ① 새 전략이 나올 때까지 기다린다 ② 정해진 판수만큼 논다 ③ 다음 전략이 올라올
때까지 기다린다 ④ 내려받아 갈아끼운다 ⑤ 반복.

**[전공] 여기서 읽을 것:**

- **`act_steps = episode_steps_per_rollout / num_workers`** — 워커를 늘려도 **한 이터레이션의
  총 에피소드 수는 그대로**다. 워커 수는 "더 많이 학습"이 아니라 "같은 양을 더 빨리"다.
  이것이 DRFC에서 `DR_WORKERS`를 올렸을 때 실제로 일어나는 일이다.
- **동기화 매개가 체크포인트 번호다.** 롤아웃 워커는 `last_checkpoint + 1`이 나타날 때까지
  폴링한다. 트레이너가 죽으면 영원히 기다리게 되므로, 루프마다 `exit_if_trainer_done`으로
  **종료 파일(`.finished` 센티넬)** 을 확인한다.

> **우리 코드와 같은 패턴.** 파일/DB를 폴링하며 상태 변화를 기다리고, 종료 신호를 별도 채널로
> 확인하는 구조는 우리 워커의 `claim_next_submission` 루프와 동형이다([06-worker.md](06-worker.md) §3).
> 다만 우리 쪽은 `FOR UPDATE SKIP LOCKED`로 경합을 DB에 맡기고, 여기서는 S3 파일 존재로 처리한다.

### 7-2. 트레이닝 워커 (sagemaker 쪽)

[`markov/training_worker.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/training_worker.py):

```python
graph_manager.save_checkpoint()          # ① 초기 체크포인트를 먼저 저장한다
graph_manager.setup_memory_backend()
while True:
    graph_manager.fetch_from_worker(graph_manager.agent_params.algorithm.num_consecutive_playing_steps)
    if graph_manager.should_train():
        ...
        graph_manager.train()
        if ...:
            log_and_exit("NaN detected in loss function, aborting training.", ...)
        graph_manager.save_checkpoint()
```

**[왜 초기 체크포인트를 먼저 저장하나]** 롤아웃 워커가 `wait_for_checkpoints()`에서 기다리고
있기 때문이다. 아무것도 없으면 양쪽이 서로를 기다리는 교착이 된다. **학습되지 않은 랜덤
가중치라도 일단 올려서 시뮬레이션을 시작시킨다.** 학습 초반에 차가 아무렇게나 달리는 이유가 이것이다.

### 7-3. Redis — 경험이 오가는 길

[`markov/deepracer_memory.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/deepracer_memory.py):

```python
WORKER_CHANNEL = 'worker_channel'

class DeepRacerRolloutBackEnd(MemoryBackend):
    ''' Class used by the rollout worker to publish data to the training worker'''
    self.topic_name = self.params.channel + '_' + self.agent_name
    self.data_pubsub.subscribe(**{WORKER_CHANNEL + '_' + self.agent_name: self.data_req_handler})
```

**양방향 pub/sub이다.**

- 롤아웃 → 트레이너: `channel_<agent>` 로 에피소드 데이터를 발행
- 트레이너 → 롤아웃: `worker_channel_<agent>` 로 **데이터 요청**을 발행

**[왜 요청/응답 구조인가]** 롤아웃이 일방적으로 밀어넣으면 트레이너가 처리하지 못한 데이터가
Redis 메모리에 쌓여 터진다. 트레이너가 "이만큼 달라"고 요청할 때만 보내는 **역압(backpressure)**
구조다. `DR_TRAIN_MAX_STEPS_PER_ITERATION`(compose의 `MAX_MEMORY_STEPS`)이 이 상한을 조절한다.

### 7-4. 알고리즘 — 무엇이 고정이고 무엇이 내 것인가

[`markov/sagemaker_graph_manager.py`](https://github.com/aws-deepracer-community/deepracer-simapp/blob/v5.3.3/bundle/markov/sagemaker_graph_manager.py):

```python
agent_params.network_wrappers['main'].learning_rate = params[HyperParameterKeys.LEARNING_RATE.value]
agent_params.network_wrappers['main'].batch_size   = params[HyperParameterKeys.BATCH_SIZE.value]
agent_params.algorithm.clip_likelihood_ratio_using_epsilon = 0.2      # ← 하드코딩
agent_params.algorithm.beta_entropy        = params[HyperParameterKeys.BETA_ENTROPY.value]
agent_params.algorithm.discount            = params[HyperParameterKeys.DISCOUNT_FACTOR.value]
agent_params.algorithm.optimization_epochs = params[HyperParameterKeys.NUM_EPOCHS.value]
...
schedule_params.improve_steps = TrainingSteps(params[...MAX_EPISODES...])
schedule_params.steps_between_evaluation_periods = EnvironmentEpisodes(40)
schedule_params.evaluation_steps = EnvironmentEpisodes(5)
schedule_params.heatup_steps = EnvironmentSteps(0)
```

| 값 | 출처 |
|---|---|
| learning rate, batch size, entropy, discount, epochs, 에피소드 수 | **`hyperparameters.json` — 참가자가 바꾼다** |
| PPO clip ε = **0.2** | **하드코딩 — 못 바꾼다** |
| 40 에피소드마다 5 에피소드 평가 | 하드코딩 |
| heatup 0 | 하드코딩 — 랜덤 탐색 워밍업 구간이 없다 |

**[전공]** 알고리즘은 **Clipped PPO**(rl_coach의 `ClippedPPOAgentParameters` 상속)다.
`heatup_steps = 0`은 의미가 있다 — 많은 RL 구현이 초반에 순수 랜덤 행동으로 버퍼를 채우는데,
DeepRacer는 첫 스텝부터 (랜덤 초기화된) 정책으로 행동한다. 초기 정책이 사실상 랜덤이라
따로 워밍업이 필요 없다는 판단이다.

신경망은 논문 기준 **컨볼루션 3층 + 완전연결 2층**을 정책망과 가치망 양쪽에 쓴다
([DeepRacer 논문](https://arxiv.org/abs/1911.01562)). 액션은 논문 기준
"스로틀 2단계 × 조향 5단계 = 10개"로 이산화하지만, 실제로는 `model_metadata.json`이 정하므로
참가자가 자유롭게 바꾼다.

---

## 8. 저장 — S3에 무엇이 남나

**[무엇을]** 학습이 진행되는 동안 MinIO 버킷의 모델 prefix 아래에 쌓이는 것들.

```text
s3://bucket/rl-deepracer-sagemaker/
├── model/                          ← 체크포인트 (이게 "모델"이다)
│   ├── .coach_checkpoint           ← 최신 체크포인트 번호를 가리키는 포인터
│   ├── N_Step-XXXX.ckpt.*          ← rl_coach 체크포인트 (학습 재개용)
│   ├── model_N.pb                  ← 추론용 frozen graph (차량 탑재/평가용)
│   ├── model_metadata.json         ← 액션 스페이스 + 센서 정의
│   └── deepracer_checkpoints.json  ← best / last 체크포인트 정보
├── ip/                             ← Redis 주소 교환용 (ip.json)
├── metrics/
│   ├── training/training-*.json
│   └── evaluation/evaluation-*.json    ← 우리 워커가 읽는 파일
├── sim_inference_logs/
│   ├── TrainingSimTraceData.csv
│   └── EvaluationSimTraceData.csv
└── mp4/
    ├── camera-pip/0-video.mp4
    ├── camera-45degree/0-video.mp4
    └── camera-topview/0-video.mp4      ← 우리 워커가 읽는 파일
```

**[왜 체크포인트와 `.pb`가 둘 다 있나]**

- **체크포인트**(`.ckpt`)는 옵티마이저 상태까지 포함한 **학습 재개용** 전체 상태다. 무겁다.
- **`.pb`**는 추론만 가능한 **frozen graph**다. 가볍고, 실제 차량과 평가에서 쓰인다.

참가자가 제출하는 250MB 아카이브가 큰 것은 체크포인트가 들어 있기 때문이다.
**평가에는 `.pb`와 `model_metadata.json`이면 충분한데도** DRFC의 `model/` 디렉터리를 통째로
내보내는 절차라 함께 딸려 온다. 이것이 우리 서비스의 500MB 상한과 `mem_limit: 900m`의
현실적 근거다([07-ops.md](07-ops.md) §3).

**[왜 `ip/`가 있나]** 롤아웃 워커와 트레이너가 만나려면 Redis 주소를 알아야 하는데, 서로 다른
컨테이너라 직접 알려줄 방법이 없다. **S3를 랑데부 지점으로 쓴다** — 트레이너가 자기 주소를
`ip.json`으로 올리고, 롤아웃이 그 파일을 폴링한다. "공유 스토리지를 서비스 디스커버리로
쓰는" 흔한 임시방편이다.

**[`best`와 `last`는 어디서 오나]** `deepracer_checkpoints.json`에 두 항목이 기록된다.
평가 시 `DR_EVAL_CHECKPOINT=best|last`가 이 파일을 참조한다. 우리 워커가
`validate_checkpoint_selection`으로 **평가를 시작하기 전에** 아카이브 안에 그 항목이
있는지 검사하는 이유가 이것이다 — 없으면 시뮬레이터는 조용히 엉뚱한 것을 쓰거나 실패한다
([06-worker.md](06-worker.md) §9-3).

---

## 9. 평가는 무엇이 다른가

`markov/evaluation_worker.py`가 별도로 있다. 학습과의 차이:

| | 학습(rollout) | 평가(evaluation) |
|---|---|---|
| 보상함수 | 매 스텝 호출 | **호출하지 않는다** |
| 탐험 | 확률적 정책에서 표집 | **결정론적** (`use_stochastic_evaluation_policy=False`) |
| 리셋 | 코스아웃하면 그 자리 근처에서 재시작 | 코스아웃하면 **그 trial은 미완주로 끝난다** |
| 시작 위치 | 진행률을 흩어 무작위 시작 | **항상 진행률 0에서 시작** (코드 주석: *"For evaluation, always start at progress 0"*) |
| 결과 | Redis로 경험 발행 | `metrics/evaluation/evaluation-*.json` 기록 |

**[왜 이 표가 실전에 중요한가]** 참가자가 "학습에서는 잘 되는데 평가에서 못 간다"고 할 때
원인의 대부분이 여기 있다.

- 학습은 **무작위 시작 위치**로 트랙 전체를 골고루 경험시킨다. 평가는 항상 처음부터 간다 →
  학습 중에는 어려운 코너를 "그 근처에서 시작해서" 여러 번 연습했지만, 평가에서는 **앞
  구간을 통과한 상태로만** 그 코너에 도착한다. 진입 속도와 자세가 다르다.
- 학습은 확률적으로 행동을 골라 가끔 다른 선택을 한다. 평가는 항상 최빈 행동만 고른다 →
  아슬아슬하게 성공하던 것이 평가에서 일관되게 실패할 수 있다.

이것이 우리 대회 규칙(**3바퀴 전부 완주**)과 만나면 결론이 하나로 모인다:
**여유 없는 빠른 모델은 온라인 예선에서 기록이 아예 남지 않는다.**

---

## 10. 우리 서비스와의 접점

`worker/drfc.py`의 주석에 적힌 가정들이 왜 그런지 이제 설명된다.

| 우리 코드의 가정 | DRFC 내부의 근거 |
|---|---|
| `metrics/evaluation/evaluation-*.json`을 읽는다 | 평가 워커가 여기에만 결과를 쓴다(§8) |
| trial 1개 = 1바퀴 | `DR_EVAL_NUMBER_OF_TRIALS`가 평가 반복 횟수이고, 각 trial이 랩 하나 |
| 영상 경로가 평가마다 덮어써진다 | `mp4/camera-*/0-video.mp4` 키가 고정 — 인덱스가 0으로 고정 |
| 앵글 3개를 우선순위로 훑는다 | 카메라 앵글마다 별도 스트림이라, 환경에 따라 특정 앵글만 실패할 수 있다(2026-07-26 사고) |
| 모델은 `{prefix}/model/` 아래여야 한다 | 평가 launch가 그 경로를 고정으로 읽는다(§8) |
| 평가 로그를 스택 삭제 전에 받아둬야 한다 | `DR_ROBOMAKER_MOUNT_LOGS=False`면 로그가 디스크에 없고 서비스 수명과 함께 사라진다 |
| `model_metadata.json`이 없으면 거부한다 | 액션 인덱스 → 조향/속도 사전이 없으면 `.pb`만으로는 주행이 불가능(§4) |

---

## 11. 자주 하는 오해 정리

| 오해 | 실제 |
|---|---|
| "보상함수가 차를 조종한다" | 조종은 신경망이 한다. 보상함수는 **끝난 뒤 채점만** 한다 |
| "`speed`는 차의 현재 속도다" | **명령한 목표 속도**다. PID로 수렴 중인 실제 속도와 다르다(§5-3) |
| "`all_wheels_on_track == False`면 에피소드 끝" | 아니다. 넷 다 나가야(`is_offtrack`) 끝난다(§2-3) |
| "코스아웃에 벌점을 줘야 한다" | 이미 정지 시간 페널티 + 에피소드 종료로 처벌된다. 이중 처벌이다(§5-5) |
| "워커를 늘리면 더 많이 학습된다" | 총 에피소드 수는 같다. **같은 양을 더 빨리** 모을 뿐이다(§7-1) |
| "보상함수를 고치면 바로 반영된다" | S3에 올려야 하고, 학습 시작 시 한 번만 적재된다(§6) |
| "트랙 파일 하나만 바꾸면 트랙이 바뀐다" | 메시(보이는 것)와 `.npy`(판정하는 것)가 따로다(§2-1) |
| "PPO 파라미터를 다 조절할 수 있다" | clip ε=0.2 등은 하드코딩이다(§7-4) |

---

## 12. 자가 점검 질문

1. robomaker와 sagemaker가 같은 도커 이미지를 쓰는 이유는? 이미지를 나누면 어떤 사고가 나나?
2. rl_coach 컨테이너가 도커 소켓을 마운트하는 이유는? 그 대가는?
3. `.npy` 파일의 6개 열은 각각 무엇이며, 그중 보상함수가 받는 것은 어디까지인가?
4. `all_wheels_on_track`과 `is_offtrack`은 각각 언제 True가 되나?
5. 신경망이 출력한 정수 `3`이 바퀴를 돌리기까지 거치는 단계를 순서대로 말해 보라.
6. 보상함수의 `speed`가 실측값이 아니라는 사실이 보상 설계에 주는 결론은?
7. `compute_current_prog`의 ±50 보정이 없으면 무슨 일이 일어나나?
8. 보상함수가 `float('nan')`을 반환하면? 그것을 그 자리에서 막는 이유는?
9. 트레이닝 워커가 **학습하기 전에** 체크포인트를 먼저 저장하는 이유는?
10. 워커 수를 2배로 늘리면 학습 결과가 2배 좋아지나?
11. 학습에서는 완주하는데 평가에서 못 가는 현상의 구조적 원인 두 가지는?
12. 제출 아카이브가 250MB나 되는 이유는? 평가에 정말 필요한 파일은 무엇인가?

---

## 13. 직접 확인해 볼 것

**실험 1 — 웨이포인트를 눈으로 보기** (5분, DRFC 없이 가능)

```bash
git clone --depth 1 --branch v5.3.3 \
  https://github.com/aws-deepracer-community/deepracer-simapp.git
python - <<'PY'
import numpy as np, matplotlib.pyplot as plt
w = np.load("deepracer-simapp/bundle/routes/reInvent2019_track.npy")
print("shape:", w.shape)          # (N, 6) 인지 확인
plt.plot(w[:,0], w[:,1], label="center")
plt.plot(w[:,2], w[:,3], label="inner")
plt.plot(w[:,4], w[:,5], label="outer")
plt.axis("equal"); plt.legend(); plt.show()
PY
```

트랙이 그려지면 §2-2를 눈으로 확인한 것이다. 이 그림이 곧
참가자 문서 07번(보상함수 그래프 검증)의 3단계 배경이 된다.

**실험 2 — 보상함수를 일부러 깨뜨리기**
`reward_function.py`가 `float('nan')`을 반환하게 하고 학습을 시작해 보라.
`dr-logs-robomaker`에 `RewardFunctionError`가 뜨고 스택이 죽는다. §5-4의 방어를 확인하는 것이다.
(NaN 대신 `1e6`을 반환하면 범위 초과 에러가 난다.)

**실험 3 — 업로드를 빼먹어 보기**
보상함수를 눈에 띄게 바꾼 뒤(예: 항상 `1e-3` 반환) **`dr-upload-custom-files` 없이**
학습을 시작하고 로그의 보상값을 본다. 바뀌지 않는다. §6의 결론 1을 몸으로 확인하는 실험이고,
참가자 질문 1위의 정체다.

**실험 4 — 컨테이너 구성 확인**

```bash
docker ps --format "table {{.Names}}\t{{.Image}}"
```

`robomaker`, `rl_coach`, `sagemaker`가 **같은 이미지**로 떠 있는지, 그리고 `sagemaker`가
compose 파일에 없는데도 존재하는지 확인한다(§1).

---

## 다음

- 우리 워커가 이 시뮬레이터를 어떻게 부리는지 → [06-worker.md](06-worker.md)
- 참가자에게 무엇을 어떤 순서로 알려줄지 → [../participant-docs-plan.md](../participant-docs-plan.md)
- 전체 지도 → [00-index.md](00-index.md)
