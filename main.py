# ------------------------------------------------------------
# 카테고리별 국내 유튜브 업로드 추이 앱
# 선택한 카테고리들이 최근 며칠간 하루에 대략 몇 개씩 올라왔는지
# 추세를 그래프로 보여줍니다.
#
# ⚠️ 정확한 통계가 아니라 "추정치"예요! (아래 방법론 설명 참고)
# ------------------------------------------------------------

import streamlit as st                         # 웹 화면(UI)을 만드는 라이브러리예요
import requests                                 # 유튜브 API에 HTTP 요청을 보낼 때 사용해요
import pandas as pd                             # 데이터를 표(DataFrame)로 다룰 때 사용해요
import plotly.express as px                     # 추이 그래프를 그릴 때 사용해요
from datetime import datetime, timedelta, timezone  # 날짜/시간 계산에 사용해요

# 브라우저 탭 제목/아이콘을 설정해요
st.set_page_config(page_title="카테고리별 국내 업로드 추이", page_icon="📈", layout="wide")

st.title("📈 카테고리별 국내 유튜브 업로드 추이")
st.caption("선택한 카테고리들이 최근 며칠간 하루에 대략 몇 개씩 올라왔는지 추세를 보여줘요.")

BASE_URL = "https://www.googleapis.com/youtube/v3"
KST = timezone(timedelta(hours=9))              # 한국 표준시(UTC+9)
MAX_SEARCH_CALLS = 90                           # search.list는 하루 100번까지만 호출 가능해서, 여유를 두고 90번으로 제한해요

# ------------------------------------------------------------
# 방법론을 접었다 폈다 볼 수 있게 안내해요. (정확도에 대한 오해를 막기 위해 중요해요)
# ------------------------------------------------------------
with st.expander("ℹ️ 이 숫자는 어떻게 계산되나요? (꼭 한번 읽어주세요)"):
    st.markdown(
        """
        1. 유튜브 검색 API(`search.list`)로 그 날짜, 그 카테고리에 올라온 영상을 검색해서
           **대략적인 전체 개수**(검색 결과 개수, 최대 100만까지 표시되는 근사치)를 가져와요.
        2. 검색 결과 중 상위 50개 영상의 **채널 국가 정보**를 확인해서,
           그중 몇 %가 국가를 'KR'로 등록해뒀는지 계산해요.
        3. 전체 개수에 그 비율을 곱해서 **"국내 채널 보정 추정치"**를 만들어요.

        채널의 국가 정보는 채널 운영자가 직접 입력하는 값이라 등록 안 한 채널도 많고,
        표본도 최대 50개뿐이라 **정확한 통계가 아니라 참고용 추정치**예요.
        """
    )

# ------------------------------------------------------------
# secrets(비밀 금고)에서 API 키를 불러와요.
# ------------------------------------------------------------
try:
    API_KEY = st.secrets["YOUTUBE_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("🔑 API 키를 찾을 수 없어요. secrets에 YOUTUBE_API_KEY가 등록되어 있는지 확인해 주세요.")
    st.stop()


@st.cache_data(ttl=86400, show_spinner=False)  # 카테고리 목록은 자주 안 바뀌니 하루 동안 캐싱해요
def get_categories(api_key):
    """
    국내(KR) 기준으로 영상에 붙일 수 있는 카테고리 목록을 가져오는 함수예요.
    {"카테고리 이름": "카테고리 ID"} 형태의 딕셔너리를 돌려줘요.
    """
    params = {"part": "snippet", "regionCode": "KR", "hl": "ko", "key": api_key}
    response = requests.get(f"{BASE_URL}/videoCategories", params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    categories = {}
    for item in data.get("items", []):
        # assignable이 True인 카테고리만 실제로 영상에 붙일 수 있는 카테고리예요
        if item["snippet"].get("assignable"):
            categories[item["snippet"]["title"]] = item["id"]
    return categories


def fetch_daily_count(api_key, category_id, day):
    """
    특정 카테고리, 특정 하루(day)에 올라온 영상 개수를 추정하는 함수예요.
    반환값: {"raw_total": 원본 추정치, "sample_size": 표본 크기,
             "kr_ratio": 국내 채널 비율(0~1, 모르면 None), "adjusted": 보정된 추정치}
    """
    # 한국 시간 기준 하루의 시작~끝을, API가 요구하는 UTC 시각으로 변환해요
    day_start_kst = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    day_start_utc = day_start_kst.astimezone(timezone.utc)
    day_end_utc = day_start_utc + timedelta(days=1)

    search_params = {
        "part": "snippet",
        "type": "video",
        "videoCategoryId": category_id,
        "regionCode": "KR",
        "publishedAfter": day_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publishedBefore": day_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maxResults": 50,
        "key": api_key,
    }
    search_response = requests.get(f"{BASE_URL}/search", params=search_params, timeout=10)
    search_response.raise_for_status()
    search_data = search_response.json()

    raw_total = search_data.get("pageInfo", {}).get("totalResults", 0)
    items = search_data.get("items", [])
    # 검색 결과에서 중복 없이 채널 ID만 뽑아내요
    channel_ids = list({item["snippet"]["channelId"] for item in items if "snippet" in item})

    kr_ratio = None
    if channel_ids:
        # channels.list는 ID를 콤마로 이어붙이면 한 번에 여러 채널을 조회할 수 있어요
        channels_params = {
            "part": "snippet",
            "id": ",".join(channel_ids),
            "key": api_key,
        }
        channels_response = requests.get(f"{BASE_URL}/channels", params=channels_params, timeout=10)
        channels_response.raise_for_status()
        channels_data = channels_response.json()

        kr_count = 0
        known_count = 0
        for channel in channels_data.get("items", []):
            country = channel.get("snippet", {}).get("country")
            if country:  # 국가를 등록해둔 채널만 비율 계산에 포함해요
                known_count += 1
                if country == "KR":
                    kr_count += 1
        if known_count > 0:
            kr_ratio = kr_count / known_count

    # 국가 정보를 하나도 못 구했으면 보정하지 않고 원본 값을 그대로 써요
    adjusted = round(raw_total * kr_ratio) if kr_ratio is not None else raw_total

    return {
        "raw_total": raw_total,
        "sample_size": len(channel_ids),
        "kr_ratio": kr_ratio,
        "adjusted": adjusted,
    }


# ------------------------------------------------------------
# 카테고리 목록을 불러와서 선택창을 만들어요.
# ------------------------------------------------------------
try:
    categories = get_categories(API_KEY)
except requests.exceptions.RequestException:
    st.error("😥 카테고리 목록을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.")
    st.stop()

if not categories:
    st.error("😥 사용 가능한 카테고리를 찾지 못했어요.")
    st.stop()

category_names = sorted(categories.keys())
# 있으면 반가운 기본 선택 카테고리 몇 개 (없으면 앞에서 3개로 대체해요)
preferred_defaults = ["게임", "음악", "뉴스/정치"]
default_selection = [name for name in preferred_defaults if name in category_names]
if not default_selection:
    default_selection = category_names[:3]

col1, col2 = st.columns([2, 1])
with col1:
    selected_names = st.multiselect(
        "살펴볼 카테고리를 선택하세요 (너무 많이 고르면 API 호출 한도를 넘길 수 있어요)",
        options=category_names,
        default=default_selection,
    )
with col2:
    num_days = st.slider("최근 며칠간의 추이를 볼까요?", min_value=3, max_value=14, value=7)

# 오늘은 아직 하루가 다 안 끝났으니 제외하고, 어제부터 거슬러 올라가요
today_kst = datetime.now(KST).date()
end_date = today_kst - timedelta(days=1)
date_list = sorted(end_date - timedelta(days=i) for i in range(num_days))

# 이번 조회로 search.list를 몇 번 호출하게 되는지 미리 계산해요
total_calls = len(selected_names) * len(date_list)
st.caption(f"이번 조회에는 검색 API 호출이 약 {total_calls}회 필요해요. (하루 한도: 100회)")

over_limit = total_calls > MAX_SEARCH_CALLS
if over_limit:
    st.warning(f"😅 카테고리 수나 기간을 줄여주세요. 지금 설정이면 {total_calls}번 호출이 필요해서 하루 한도를 넘길 수 있어요.")

run_button = st.button(
    "추이 조회하기",
    type="primary",
    disabled=(over_limit or not selected_names),
)

# ------------------------------------------------------------
# 버튼을 누르면 카테고리 × 날짜 조합마다 데이터를 가져와요.
# ------------------------------------------------------------
if run_button:
    rows = []
    progress_bar = st.progress(0.0)
    status_text = st.empty()

    quota_exceeded = False
    total_steps = len(selected_names) * len(date_list)
    step = 0

    for category_name in selected_names:
        category_id = categories[category_name]
        for day in date_list:
            step += 1
            status_text.text(f"'{category_name}' 카테고리의 {day} 데이터를 가져오는 중... ({step}/{total_steps})")

            try:
                result = fetch_daily_count(API_KEY, category_id, day)
                rows.append({
                    "날짜": day,
                    "카테고리": category_name,
                    "원본 추정치": result["raw_total"],
                    "보정 추정치": result["adjusted"],
                    "표본 크기": result["sample_size"],
                    "국내채널 비율": result["kr_ratio"],
                })
            except requests.exceptions.HTTPError as error:
                status_code = error.response.status_code if error.response is not None else None
                if status_code == 403:
                    # 할당량 초과는 더 호출해봐야 계속 실패하니, 여기서 바로 멈춰요
                    quota_exceeded = True
                    break
                # 그 외 오류는 이 데이터 포인트만 건너뛰고 계속 진행해요
            except requests.exceptions.RequestException:
                # 네트워크 오류도 이 데이터 포인트만 건너뛰고 계속 진행해요
                pass

            progress_bar.progress(step / total_steps)

        if quota_exceeded:
            break

    progress_bar.empty()
    status_text.empty()

    if quota_exceeded:
        st.error("😥 유튜브 API 하루 할당량을 다 써버린 것 같아요. 내일 다시 시도해 주세요.")

    if not rows:
        if not quota_exceeded:
            st.error("😥 데이터를 가져오지 못했어요. 잠시 후 다시 시도해 주세요.")
    else:
        df = pd.DataFrame(rows)

        # ------------------------------------------------------------
        # 카테고리별 추이를 plotly 꺾은선 그래프로 그려요.
        # ------------------------------------------------------------
        fig = px.line(
            df,
            x="날짜",
            y="보정 추정치",
            color="카테고리",
            markers=True,
            title="카테고리별 국내 채널 보정 업로드 추정치 추이",
        )
        fig.update_layout(
            xaxis_title="날짜",
            yaxis_title="업로드 개수 (추정치)",
            legend_title="카테고리",
        )
        st.plotly_chart(fig, use_container_width=True)

        # ------------------------------------------------------------
        # 상세 데이터도 표로 함께 보여줘요 (투명성을 위해 원본 값도 같이 보여줘요)
        # ------------------------------------------------------------
        st.subheader("📋 상세 데이터")
        display_df = df.copy()
        display_df["국내채널 비율"] = display_df["국내채널 비율"].apply(
            lambda ratio: f"{ratio:.0%}" if pd.notna(ratio) else "알 수 없음"
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)
