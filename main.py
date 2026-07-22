# ------------------------------------------------------------
# 날짜별 좋아요 1위 영상 모아보기 앱
# 최근 30일 동안, 날짜마다 좋아요 수가 가장 많은 영상을
# "국내" 기준과 "해외 전체" 기준으로 각각 찾아서 보여줍니다.
# 영상을 클릭하면 새 탭에서 유튜브 영상이 재생돼요.
#
# ⚠️ 정확한 전체 통계가 아니라, 조회수 상위 후보들 중에서 찾은
#    "가장 그럴듯한 후보"예요. (아래 방법론 설명 참고)
# ------------------------------------------------------------

import streamlit as st                         # 웹 화면(UI)을 만드는 라이브러리예요
import requests                                 # 유튜브 API에 HTTP 요청을 보낼 때 사용해요
import time                                     # 요청 사이에 잠깐 쉬어가기 위해 사용해요
from datetime import datetime, timedelta, timezone  # 날짜/시간 계산에 사용해요

# 브라우저 탭 제목/아이콘을 설정해요
st.set_page_config(page_title="날짜별 좋아요 1위 영상", page_icon="👍", layout="wide")

st.title("👍 날짜별 좋아요 1위 영상")
st.caption("최근 며칠간, 날짜마다 좋아요가 가장 많았던 영상을 국내/해외 기준으로 찾아드려요.")

BASE_URL = "https://www.googleapis.com/youtube/v3"
KST = timezone(timedelta(hours=9))              # 한국 표준시(UTC+9)
MAX_SEARCH_CALLS = 90                           # search.list는 하루 100번까지만 호출 가능해서, 여유를 두고 90번으로 제한해요

# ------------------------------------------------------------
# 방법론을 접었다 폈다 볼 수 있게 안내해요. (정확도에 대한 오해를 막기 위해 중요해요)
# ------------------------------------------------------------
with st.expander("ℹ️ 이 결과는 어떻게 찾나요? (꼭 한번 읽어주세요)"):
    st.markdown(
        """
        유튜브 검색 API에는 "좋아요 순으로 정렬"하는 기능이 없어요. 그래서 이렇게 우회해요.

        1. 그 날짜에 올라온 영상 중 **조회수 상위 50개**를 검색해서 후보로 뽑아요.
        2. 그 50개 영상의 **실제 좋아요 수**를 하나하나 확인해서, 가장 많은 영상을 골라요.

        조회수 최상위권 안에 그날의 좋아요 1위도 있을 가능성이 매우 높다는 가정에 기반한 방식이라,
        **아주 드물게 진짜 1위를 놓칠 수도 있어요.**

        **"국내" 기준의 의미**: `regionCode=KR`(한국에서 시청 가능한 영상) + 한국어 가중치를 사용해요.
        이건 "한국에서 만들어진 영상"이 아니라 **"한국에서 볼 수 있는 영상"**에 가까운 의미예요
        (대부분의 유튜브 영상은 전세계에서 다 보이기 때문에, 완벽한 국내 필터는 아니에요).

        **"해외 전체" 기준의 의미**: 국가 제한을 두지 않고 검색한 결과예요.

        **좋아요 수가 안 보이는 영상**: 업로더가 좋아요 수를 비공개로 설정했다면 0으로 집계돼서,
        실제로는 인기 있어도 순위에서 밀릴 수 있어요.
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


def request_with_retry(url, params, max_retries=4):
    """
    requests.get을 대신 호출해주는 함수예요. 429(요청이 너무 잦음) 오류가 나면
    조금씩 더 길게 기다렸다가 자동으로 다시 시도해요. (하루 할당량 초과인 403과는 달라서,
    잠깐 기다리면 대부분 해결돼요)
    """
    response = None
    for attempt in range(max_retries):
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 429:
            time.sleep(1.5 * (attempt + 1))  # 1.5초, 3초, 4.5초, 6초로 점점 길게 기다려요
            continue
        response.raise_for_status()
        return response

    # 여기까지 왔다는 건 마지막 시도까지도 429였다는 뜻이에요
    response.raise_for_status()
    return response


def fetch_top_video_of_day(api_key, day, region_code=None, relevance_language=None):
    """
    특정 날짜(day)에 올라온 영상 중 좋아요 수가 가장 많은 영상 하나를 찾는 함수예요.
    1) search.list로 그 날 올라온 영상 중 조회수 상위 50개를 후보로 뽑아요.
    2) videos.list로 그 50개의 실제 좋아요 수를 확인해서 최댓값을 골라요.
    찾으면 영상 정보 딕셔너리를, 후보가 하나도 없으면 None을 돌려줘요.
    """
    day_start_kst = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    day_start_utc = day_start_kst.astimezone(timezone.utc)
    day_end_utc = day_start_utc + timedelta(days=1)

    search_params = {
        "part": "snippet",
        "type": "video",
        "order": "viewCount",  # 좋아요 순 정렬이 없어서, 조회수 순 상위권을 후보로 삼아요
        "publishedAfter": day_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publishedBefore": day_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maxResults": 50,
        "key": api_key,
    }
    if region_code:
        search_params["regionCode"] = region_code
    if relevance_language:
        search_params["relevanceLanguage"] = relevance_language

    search_response = request_with_retry(f"{BASE_URL}/search", search_params)
    search_data = search_response.json()

    video_ids = [
        item["id"]["videoId"]
        for item in search_data.get("items", [])
        if item.get("id", {}).get("videoId")
    ]
    if not video_ids:
        return None

    # videos.list는 한 번에 최대 50개 ID까지 조회할 수 있어요 (search 결과도 최대 50개라 딱 맞아요)
    videos_params = {
        "part": "snippet,statistics",
        "id": ",".join(video_ids),
        "key": api_key,
    }
    videos_response = request_with_retry(f"{BASE_URL}/videos", videos_params)
    videos_data = videos_response.json()

    best_video = None
    best_likes = -1
    for item in videos_data.get("items", []):
        like_count = int(item.get("statistics", {}).get("likeCount", 0))
        if like_count > best_likes:
            best_likes = like_count
            best_video = item

    if best_video is None:
        return None

    thumbnails = best_video["snippet"].get("thumbnails", {})
    thumbnail_url = (
        thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
    ).get("url")

    return {
        "video_id": best_video["id"],
        "title": best_video["snippet"]["title"],
        "channel_title": best_video["snippet"]["channelTitle"],
        "thumbnail_url": thumbnail_url,
        "like_count": best_likes,
        "url": f"https://www.youtube.com/watch?v={best_video['id']}",
    }


# ------------------------------------------------------------
# 조회 기간을 정해요.
# ------------------------------------------------------------
num_days = st.slider("최근 며칠간을 살펴볼까요?", min_value=7, max_value=30, value=30)

# 오늘은 아직 하루가 다 안 끝났으니 제외하고, 어제부터 거슬러 올라가요
today_kst = datetime.now(KST).date()
end_date = today_kst - timedelta(days=1)
date_list = sorted(end_date - timedelta(days=i) for i in range(num_days))

# 하루당 국내/해외 2번씩 검색하니, 총 호출 수는 날짜 수 × 2예요
total_calls = num_days * 2
st.caption(f"이번 조회에는 검색 API 호출이 {total_calls}회 필요해요. (하루 한도: 100회)")

over_limit = total_calls > MAX_SEARCH_CALLS
if over_limit:
    st.warning(f"😅 기간을 줄여주세요. 지금 설정이면 {total_calls}번 호출이 필요해서 하루 한도를 넘길 수 있어요.")

run_button = st.button("조회하기", type="primary", disabled=over_limit)

# ------------------------------------------------------------
# 버튼을 누르면 날짜마다 국내/해외 1위 영상을 찾아요.
# ------------------------------------------------------------
if run_button:
    results = []  # [{"날짜": ..., "국내": {...} or None, "해외 전체": {...} or None}, ...]
    quota_exceeded = False

    progress_bar = st.progress(0.0)
    status_text = st.empty()
    total_steps = len(date_list) * 2
    step = 0

    for day in date_list:
        day_result = {"날짜": day}
        for bucket_label, region_code, relevance_language in [
            ("국내", "KR", "ko"),
            ("해외 전체", None, None),
        ]:
            step += 1
            status_text.text(f"{day} · {bucket_label} 데이터를 가져오는 중... ({step}/{total_steps})")

            video = None
            error_note = None
            try:
                video = fetch_top_video_of_day(API_KEY, day, region_code, relevance_language)
                if video is None:
                    error_note = "empty"  # 검색 자체는 성공했지만, 그 날짜엔 결과가 하나도 없었어요
            except requests.exceptions.HTTPError as error:
                status_code = error.response.status_code if error.response is not None else None
                if status_code == 403:
                    quota_exceeded = True
                    break  # 안쪽(국내/해외) 반복문만 멈춰요
                error_note = f"http_{status_code}"
            except requests.exceptions.RequestException:
                error_note = "network"  # 네트워크 오류 등

            day_result[bucket_label] = video
            day_result[f"{bucket_label}_오류"] = error_note
            progress_bar.progress(step / total_steps)
            time.sleep(0.3)  # 요청이 너무 몰리지 않게 짧게 쉬어가요

        results.append(day_result)
        if quota_exceeded:
            break  # 바깥(날짜) 반복문도 멈춰요

    progress_bar.empty()
    status_text.empty()

    if quota_exceeded:
        st.error("😥 유튜브 API 하루 할당량을 다 써버린 것 같아요. 내일 다시 시도해 주세요.")

    if not results:
        st.error("😥 데이터를 가져오지 못했어요. 잠시 후 다시 시도해 주세요.")
    else:
        # ------------------------------------------------------------
        # 날짜별로 국내/해외 영상을 카드 형태로 보여줘요. (최근 날짜가 위로 오게)
        # ------------------------------------------------------------
        def render_video_card(column, video, error_note=None):
            """영상 정보를 썸네일 + 제목 + 좋아요 수 + '새 탭에서 보기' 버튼으로 보여주는 함수예요."""
            with column:
                if video is None:
                    if error_note == "empty":
                        st.caption("이 날짜엔 검색 결과 자체가 없었어요. (색인 반영 지연일 수 있어요)")
                    elif error_note == "network":
                        st.caption("⚠️ 네트워크 오류로 가져오지 못했어요.")
                    elif error_note:
                        st.caption(f"⚠️ 오류로 가져오지 못했어요 (코드: {error_note}).")
                    else:
                        st.caption("데이터를 찾지 못했어요.")
                    return
                if video["thumbnail_url"]:
                    st.image(video["thumbnail_url"], use_container_width=True)
                st.markdown(f"**{video['title']}**")
                st.caption(f"{video['channel_title']} · 👍 {video['like_count']:,}")
                # link_button은 기본적으로 새 탭에서 링크를 열어줘요
                st.link_button("▶ 유튜브에서 보기", video["url"], use_container_width=True)

        for day_result in reversed(results):  # 최근 날짜가 맨 위로 오게 뒤집어요
            st.divider()
            st.subheader(str(day_result["날짜"]))
            col_domestic, col_global = st.columns(2)

            with col_domestic:
                st.markdown("#### 🇰🇷 국내")
            render_video_card(col_domestic, day_result.get("국내"), day_result.get("국내_오류"))

            with col_global:
                st.markdown("#### 🌎 해외 전체")
            render_video_card(col_global, day_result.get("해외 전체"), day_result.get("해외 전체_오류"))
