# ------------------------------------------------------------
# 카테고리별 국내 유튜브 업로드 추이 앱
# 선택한 카테고리들이 최근 며칠간 하루에 대략 몇 개씩 올라왔는지
# 추세를 그래프로 보여줍니다.
#
# ⚠️ 정확한 통계가 아니라 "표본 기반 추정치"예요! (아래 방법론 설명 참고)
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
MAX_PAGES_PER_CATEGORY = 4                      # 카테고리 하나당 최대 4페이지(최대 200개 영상)까지만 가져와요

# ------------------------------------------------------------
# 방법론을 접었다 폈다 볼 수 있게 안내해요. (정확도에 대한 오해를 막기 위해 중요해요)
# ------------------------------------------------------------
with st.expander("ℹ️ 이 숫자는 어떻게 계산되나요? (꼭 한번 읽어주세요)"):
    st.markdown(
        """
        **왜 하루씩 쪼개서 검색하지 않나요?**
        처음엔 카테고리마다 하루하루 따로 검색했는데, 그렇게 좁혀서 검색하면
        실제로 영상이 있어도 검색 API가 결과를 거의 못 찾아오는 경우가 많았어요
        (특히 최근 날짜일수록 더 심했어요). 그래서 지금은 카테고리마다
        **선택한 기간 전체를 한 번에, 최신순으로 최대 {max_pages}페이지(최대 {max_videos}개)까지** 가져온 뒤,
        각 영상의 실제 업로드 날짜를 보고 직접 날짜별로 묶어서 세는 방식으로 바꿨어요.

        **"국내" 판단은 어떻게 하나요?**
        가져온 각 영상의 채널이 국가를 'KR'로 등록해뒀는지 하나하나 직접 확인해요.
        국가를 등록 안 한 채널은 실제로 국내 채널이어도 '국내'로 안 잡혀요.

        **한계점**
        - 카테고리당 최대 {max_videos}개까지만 가져오기 때문에, 영상이 아주 많은 인기 카테고리는
          다 못 세고, 특히 기간 중 **더 오래된 날짜일수록 덜 잡힐 수** 있어요 (최신순으로 가져오기 때문이에요).
        - 채널 국가는 운영자가 직접 등록한 값이라, 등록 안 한 채널은 국내여도 카운트에서 빠져요.
        - `search.list` 자체가 유튜브의 모든 영상을 완벽하게 색인해두는 게 아니라서,
          여기 나오는 숫자는 **정확한 전체 통계가 아니라 참고용 추정치**예요.
        """.format(max_pages=MAX_PAGES_PER_CATEGORY, max_videos=MAX_PAGES_PER_CATEGORY * 50)
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


def fetch_category_videos(api_key, category_id, published_after, published_before, max_pages):
    """
    특정 카테고리에서 지정한 기간 동안 올라온 영상들을, 최신순으로 최대 max_pages 페이지까지 가져와요.
    반환값: (영상 목록, 표본이 잘렸는지 여부)
    영상 목록의 각 항목은 {"channel_id": ..., "published_at": ...} 형태예요.
    """
    videos = []
    page_token = None
    pages_fetched = 0

    while pages_fetched < max_pages:
        params = {
            "part": "snippet",
            "type": "video",
            "videoCategoryId": category_id,
            "regionCode": "KR",
            "order": "date",  # 최신순으로 가져와요 (검색어가 없을 땐 'relevance'가 불안정해서요)
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            "maxResults": 50,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        response = requests.get(f"{BASE_URL}/search", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            videos.append({
                "channel_id": snippet.get("channelId"),
                "published_at": snippet.get("publishedAt"),
            })

        pages_fetched += 1
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    truncated = bool(page_token)  # 루프가 끝났는데도 다음 페이지가 남아있다면, 표본이 잘린 거예요
    return videos, truncated


def get_channel_countries(api_key, channel_ids):
    """
    채널 ID 목록을 받아서 {채널ID: 국가코드 또는 None} 딕셔너리를 돌려주는 함수예요.
    channels.list는 한 번에 최대 50개 ID까지 조회할 수 있어서, 50개씩 나눠서 요청해요.
    """
    countries = {}
    channel_ids = list(channel_ids)

    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]
        params = {"part": "snippet", "id": ",".join(batch), "key": api_key}
        response = requests.get(f"{BASE_URL}/channels", params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        for channel in data.get("items", []):
            countries[channel["id"]] = channel.get("snippet", {}).get("country")

    return countries


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
        "살펴볼 카테고리를 선택하세요 (3~5개 정도가 그래프 보기에 좋아요)",
        options=category_names,
        default=default_selection,
    )
with col2:
    num_days = st.slider("최근 며칠간의 추이를 볼까요?", min_value=3, max_value=14, value=7)

# 오늘은 아직 하루가 다 안 끝났으니 제외하고, 어제부터 거슬러 올라가요
today_kst = datetime.now(KST).date()
end_date = today_kst - timedelta(days=1)
date_list = sorted(end_date - timedelta(days=i) for i in range(num_days))

# 이번 조회로 search.list를 몇 번 호출하게 되는지(최대치 기준) 미리 계산해요
total_calls = len(selected_names) * MAX_PAGES_PER_CATEGORY
st.caption(f"이번 조회에는 검색 API 호출이 최대 {total_calls}회 필요해요. (하루 한도: 100회)")

over_limit = total_calls > MAX_SEARCH_CALLS
if over_limit:
    st.warning(f"😅 카테고리 수를 줄여주세요. 지금 설정이면 최대 {total_calls}번 호출이 필요해서 하루 한도를 넘길 수 있어요.")

run_button = st.button(
    "추이 조회하기",
    type="primary",
    disabled=(over_limit or not selected_names),
)

# ------------------------------------------------------------
# 버튼을 누르면 카테고리마다 기간 전체를 한 번에 조회해요.
# ------------------------------------------------------------
if run_button:
    # 선택한 날짜 범위 전체를 한국 시간 기준으로 잡고, API가 요구하는 UTC로 변환해요
    range_start_kst = datetime.combine(date_list[0], datetime.min.time(), tzinfo=KST)
    range_end_kst = datetime.combine(date_list[-1] + timedelta(days=1), datetime.min.time(), tzinfo=KST)
    published_after = range_start_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    published_before = range_end_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = []
    truncated_categories = []
    quota_exceeded = False

    progress_bar = st.progress(0.0)
    status_text = st.empty()

    for idx, category_name in enumerate(selected_names):
        category_id = categories[category_name]
        status_text.text(f"'{category_name}' 카테고리 데이터를 가져오는 중...")

        videos, truncated = [], False
        try:
            videos, truncated = fetch_category_videos(
                API_KEY, category_id, published_after, published_before, MAX_PAGES_PER_CATEGORY
            )
        except requests.exceptions.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else None
            if status_code == 403:
                # 할당량 초과는 더 시도해봐야 계속 실패하니, 여기서 바로 멈춰요
                quota_exceeded = True
                break
            # 그 외 오류는 이 카테고리만 건너뛰고 계속 진행해요
        except requests.exceptions.RequestException:
            pass

        if truncated:
            truncated_categories.append(category_name)

        # 이 카테고리에서 나온 영상들의 채널 국가를 한 번에 조회해요
        channel_ids = {video["channel_id"] for video in videos if video["channel_id"]}
        channel_countries = {}
        if channel_ids:
            try:
                channel_countries = get_channel_countries(API_KEY, channel_ids)
            except requests.exceptions.RequestException:
                channel_countries = {}

        # 영상들을 한국 시간 기준 날짜별로 묶어서 세요
        counts_by_day = {day: {"raw": 0, "kr": 0} for day in date_list}
        for video in videos:
            if not video["published_at"]:
                continue
            published_dt_utc = datetime.strptime(
                video["published_at"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            published_date_kst = published_dt_utc.astimezone(KST).date()

            if published_date_kst not in counts_by_day:
                continue  # 요청 범위 경계에 살짝 걸친 영상은 무시해요

            counts_by_day[published_date_kst]["raw"] += 1
            if channel_countries.get(video["channel_id"]) == "KR":
                counts_by_day[published_date_kst]["kr"] += 1

        for day in date_list:
            rows.append({
                "날짜": day,
                "카테고리": category_name,
                "표본 내 영상 수": counts_by_day[day]["raw"],
                "국내채널 영상 수": counts_by_day[day]["kr"],
            })

        progress_bar.progress((idx + 1) / len(selected_names))

    progress_bar.empty()
    status_text.empty()

    if quota_exceeded:
        st.error("😥 유튜브 API 하루 할당량을 다 써버린 것 같아요. 내일 다시 시도해 주세요.")

    if truncated_categories:
        st.info(
            "ℹ️ " + ", ".join(truncated_categories)
            + " 카테고리는 영상이 너무 많아서 일부만 가져왔어요. "
            + "최신순으로 가져오기 때문에, 기간 중 더 오래된 날짜는 실제보다 적게 잡혔을 수 있어요."
        )

    if not rows:
        if not quota_exceeded:
            st.error("😥 데이터를 가져오지 못했어요. 잠시 후 다시 시도해 주세요.")
    else:
        df = pd.DataFrame(rows)

        # ------------------------------------------------------------
        # 카테고리별 국내채널 영상 수 추이를 plotly 꺾은선 그래프로 그려요.
        # ------------------------------------------------------------
        fig = px.line(
            df,
            x="날짜",
            y="국내채널 영상 수",
            color="카테고리",
            markers=True,
            title="카테고리별 국내채널 업로드 추이 (표본 기준)",
        )
        fig.update_layout(
            xaxis_title="날짜",
            yaxis_title="국내채널 영상 수 (표본 내)",
            legend_title="카테고리",
        )
        st.plotly_chart(fig, use_container_width=True)

        # ------------------------------------------------------------
        # 상세 데이터도 표로 함께 보여줘요 (투명성을 위해 표본 전체 개수도 같이 보여줘요)
        # ------------------------------------------------------------
        st.subheader("📋 상세 데이터")
        st.dataframe(df, use_container_width=True, hide_index=True)
