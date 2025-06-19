import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import os

# JIRA 접속 정보 설정
base_url = "https://higen-rnd.atlassian.net/rest/api/2/"
with open('jira_api_token.txt', 'r', encoding='utf-8') as f1:
    jira_api_token = f1.read()
with open('user_email.txt', 'r', encoding='utf-8') as f2:
    user_email = f2.read()

# 사용자 키 (Atlassian 사용자 ID) 및 사용자 이름 획득
HEADERS = {"Content-Type": "application/json"}
response1 = requests.get(f"{base_url}/user/search", auth=(user_email,jira_api_token), headers=HEADERS, params={"query": user_email})
if response1.status_code == 200 and response1.json():
    username_key = response1.json()[0]["accountId"]
    displayName_to_check = response1.json()[0]["displayName"]
else:
    if os.path.exists("acopuntID.txt"):
        with open("acopuntID.txt", "r") as f3:
            username_key = f3.read().strip()
    else:
        print("# 아래 URL을 웹브라우저에 복사해 넣어서 acountID를 확인하세요.")
        print(f"https://higen-rnd.atlassian.net/rest/api/2/user/search?query={user_email}")
        username_key = input("# acountID를 입력하세요 : ")
        with open("acopuntID.txt", "w") as f4:
            f4.write(username_key)

# 조회 날짜 설정 (조회하고 싶은 일자로 수정 가능)
print("# 원하는 날짜를 입력하세요.")
print("  예 : 2025-06-16")
print(f"  그냥 엔터를 치면 오늘 기준입니다 : {datetime.now().strftime("%Y-%m-%d")}")
date_input = input()
if date_input == "":
    date_input = datetime.now()
    d = date_input
else:
    try:
        d = datetime.strptime(date_input, "%Y-%m-%d")
    except ValueError:
        print("  날짜 형식이 올바르지 않습니다. (예: 2025-06-16)")
print("  입력한 날짜:", d.date())
today = datetime(d.year, d.month, d.day, 23, 59, 59)

start_date_str = (today - timedelta(days=0)).strftime("%Y-%m-%d")
end_date_str = today.strftime("%Y-%m-%d")

# 문자열 날짜를 datetime 객체로 변환
# 주의: Jira에서 반환되는 started_time은 '2024-06-13T14:00:00.000+0900' 형태입니다.

# 시작 날짜의 자정 (00:00:00)
start_filter_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
# 종료 날짜의 다음 날 자정 (즉, end_date_str의 모든 시간을 포함하도록)
end_filter_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)

# 누적 시간
total_time_spent_seconds = 0 # 합산할 시간을 저장할 변수 초기화

# JQL 쿼리 작성 (단순 날짜 형식 사용)
jql_query = f"worklogAuthor = '{username_key}' AND timeSpent > 0 AND worklogDate >= '{start_date_str}' AND worklogDate <= '{end_date_str}'"

# API 요청 헤더 설정
headers = {"Accept": "application/json",}

# API 요청 본문 설정
body = {
    "jql": jql_query,
    "startAt": 0,       # 첫 번째 항목부터 시작
    "maxResults": 100,  # 한 번에 최대 100개의 결과 가져오기    
    "fields": ["project", "worklog"],  # 필요한 필드 명시적으로 지정
}

# API 요청 보내기
response = requests.post(base_url + "search", json=body, headers=headers, auth=HTTPBasicAuth(user_email, jira_api_token))

# 총 업무시간 확인 : total_time_spent_seconds
if response.status_code == 200:
    # JSON 데이터 파싱 시작
    data = response.json()
    # 각 이슈에 대해 Worklog 정보 추출
    for issue in data['issues']:
        response2 = requests.get(base_url + "issue/" + issue['key'], auth=HTTPBasicAuth(user_email, jira_api_token))
        # Worklog 정보 출력
        if 'worklog' in issue['fields'] and 'worklogs' in issue['fields']['worklog']:
            for worklog in issue['fields']['worklog']['worklogs']:
                if worklog['author']['displayName'] == displayName_to_check: # 'displayName_to_check' 변수를 사용
                    worklog_started_datetime = datetime.fromisoformat(worklog['started'])
                    worklog_started_naive_datetime = worklog_started_datetime.replace(tzinfo=None)
                    if start_filter_date <= worklog_started_naive_datetime <= end_filter_date:
                        time_spent_seconds = worklog.get('timeSpentSeconds', 0) # timeSpentSeconds가 없을 경우 0으로 처리
                        # 시간(timeSpentSeconds)을 합산
                        total_time_spent_seconds += time_spent_seconds # 합산

# 기존 총 업무시간 출력
# 총 시간을 시간, 분, 초로 변환
total_hours = total_time_spent_seconds // 3600
remaining_minutes = (total_time_spent_seconds % 3600) // 60
seconds = total_time_spent_seconds % 60
try: total_hours
except NameError:
    total_hours = 0
    remaining_minutes = 0
    seconds = 0
print(f"  기존 총 업무시간 : {total_hours}:{remaining_minutes}:{seconds}")

# 원하는 총 업무시간 설정 (0을 입력하면 원래값 그대로 유지)
print("# 0을 입력하면 원래값 그대로 유지됩니다.")
workingtime_input = input(f"  원하는 총 업무시간을 입력하세요 (예:{total_hours}) : ")
workingtime_seconds = int(workingtime_input)*3600

# 새로 입력한 총 업무시간에서 기존의 총 업무시간을 나눈 비율
new_total_hours = workingtime_seconds // 3600
new_remaining_minutes = (workingtime_seconds % 3600) // 60
new_seconds = workingtime_seconds % 60
try: new_total_hours
except NameError:
    new_total_hours = 0
    new_remaining_minutes = 0
    new_seconds = 0
print(f"  원하는 총 업무시간 : {new_total_hours}:{new_remaining_minutes}:{new_seconds}")
if workingtime_seconds==0 or total_time_spent_seconds==0:
    workingtime_factor = 1
else:
    workingtime_factor = workingtime_seconds / total_time_spent_seconds

# 총 업무시간 조정
if response.status_code == 200:
    # JSON 데이터 파싱 시작
    data = response.json()
    # 각 이슈에 대해 Worklog 정보 추출
    for issue in data['issues']:
        response4 = requests.get(base_url + "issue/" + issue['key'], auth=HTTPBasicAuth(user_email, jira_api_token))
        # Worklog 정보 출력
        if 'worklog' in issue['fields'] and 'worklogs' in issue['fields']['worklog']:
            for worklog in issue['fields']['worklog']['worklogs']:
                if worklog['author']['displayName'] == displayName_to_check: # 'displayName_to_check' 변수를 사용
                    worklog_started_datetime = datetime.fromisoformat(worklog['started'])
                    worklog_started_naive_datetime = worklog_started_datetime.replace(tzinfo=None)
                    if start_filter_date <= worklog_started_naive_datetime <= end_filter_date:
                        if workingtime_seconds != 0:
                             time_spent_seconds = worklog.get('timeSpentSeconds', 0) # timeSpentSeconds가 없을 경우 0으로 처리
                             new_workingtime_seconds = int(int(time_spent_seconds) * workingtime_factor)
                             response4 = requests.put(base_url+"issue/"+issue['key']+"/worklog/"+worklog['id'], headers={"Accept": "application/json","Content-Type": "application/json"}, auth=HTTPBasicAuth(user_email,jira_api_token), json={"timeSpentSeconds": new_workingtime_seconds})
                             #print(json.dumps(response4.json(), indent=4, ensure_ascii=False))

# API 요청 보내기
response = requests.post(base_url + "search", json=body, headers=headers, auth=HTTPBasicAuth(user_email, jira_api_token))

# 응답 확인 및 출력
total_time_spent_seconds = 0
if response.status_code == 200:
    print("\n\nRequest successfully received!")
    print(f"Response status code: {response.status_code}")
    # JSON 데이터 파싱 시작
    data = response.json()
    print("\nAPI Response:\n")
    # 각 이슈에 대해 Worklog 정보 추출
    for issue in data['issues']:
        print(f"# Project Name: {issue['fields']['project']['name']}")
        print(f"# Issue Key: {issue['key']}")
        response2 = requests.get(base_url + "issue/" + issue['key'], auth=HTTPBasicAuth(user_email, jira_api_token))
        print(f"# Issue Summary: {response2.json()['fields']['summary']}")
        # Worklog 정보 출력
        if 'worklog' in issue['fields'] and 'worklogs' in issue['fields']['worklog']:
            print("# Worklogs exist:")
            for worklog in issue['fields']['worklog']['worklogs']:
                # displayName 변수가 미리 정의되어 있다고 가정합니다.
                # 예: displayName_to_check = "주형렬C"
                if worklog['author']['displayName'] == displayName_to_check: # 'displayName_to_check' 변수를 사용
                    #started_time가 start_date ~ end_date 사이에 있는 것만 필터링
                    # Jira의 started_time은 ISO 8601 형식입니다. fromisoformat()이 가장 적합합니다.
                    # 시간대 정보가 포함되어 있으므로 aware datetime 객체로 파싱됩니다.
                    worklog_started_datetime = datetime.fromisoformat(worklog['started'])
                    # 가장 간단한 방법은 worklog_started_datetime에서 시간대 정보를 제거하고 날짜만 비교하는 것입니다.
                    # 또는, 필터 날짜와 시간까지 모두 비교하려면,
                    # worklog_started_datetime을 naive datetime으로 만들거나
                    # 필터 날짜를 worklog_started_datetime과 동일한 시간대로 맞춰줘야 합니다.
                    # 여기서는 worklog_started_datetime을 naive datetime으로 만들어서 비교하는 방법을 사용합니다.
                    # (대부분의 경우 이렇게 해도 무방하나, 정확한 시간대 처리가 필요하면 복잡해짐)
                    worklog_started_naive_datetime = worklog_started_datetime.replace(tzinfo=None)
                    if start_filter_date <= worklog_started_naive_datetime <= end_filter_date:
                        print(f"  Worklog by {worklog['author']['displayName']}")
                        print(f"  Started: {worklog['started']}")
                        time_spent_seconds = worklog.get('timeSpentSeconds', 0) # timeSpentSeconds가 없을 경우 0으로 처리
                        print(f"  Time Spent: {time_spent_seconds:-} seconds")
                        if 'comment' in worklog:
                            print(f"## Comment: \n{worklog['comment']}")
                        else:
                            print(f"## Comment: ")
                        # 시간(timeSpentSeconds)을 합산
                        total_time_spent_seconds += time_spent_seconds # 합산
            print("------------------------------------------------")
            print(f"# Total time spent by {displayName_to_check}: {total_time_spent_seconds} seconds")
            print("\n")
        else:
            print("# No Worklogs found.")
        # 총 시간을 시간, 분, 초로 변환
        total_hours = total_time_spent_seconds // 3600
        remaining_minutes = (total_time_spent_seconds % 3600) // 60
        seconds = total_time_spent_seconds % 60
    try: total_hours
    except NameError:
        total_hours = 0
        remaining_minutes = 0
        seconds = 0
    print(f"\n# Total time spent on issues: \n{total_hours}:{remaining_minutes}:{seconds}")
else:
    print(f"Failed to fetch data: {response.status_code}, {response.text}")

