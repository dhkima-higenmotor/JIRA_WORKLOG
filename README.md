
# 개요

# 실행 조건
* 파이썬 패키지 관리자 uv가 설치되어 있을 것

## uv 설치 방법
* scoop를 이용하여 설치 (유저권한,권장)
```
scoop install uv
```

* 정규 배포판으로 설치 방법
```
# Powershell을 관리자 권한으로 실행한 후, 아래 명령어 입력해서 실행
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```


# 사용방법

## jira_api_token 만들기
* Jira 브라우저 페이지 오른쪽 상단의 자신의 아이디 아이콘을 누른다.
* `Manage acount`를 누른다.
* 상단의 `Security`를 누른다.
* `Create and manage API tokens`를 누른다.
* `Create API token`을 누른다.
* `Name`을 적당하게 적어주고, `Expired on`은 충분히 오랜 날짜로 설정해 준다.
* `Create`를 눌러준다.
* 생성된 API token을 복사해서, 잘 보관해 둔다.  두 번 다시 보여주지 않기 때문에 분실할 경우 새로 생성해야 한다.

## 자동 로그인 설정파일 만들기
* 텍스트 에디터로 `jira_api_token.txt` 파일을 생성하고, 앞서 생성한 jira_api_token 코드를 복사해 넣고 저장한다.
* 텍스트 에디터로 `user_email.txt` 파일을 생성하고, 자신의 회사 이메일 주소를 써넣고 저장한다.

## 실행아이콘 만들기
* `JIRA_WORKLOG.bat` 파일의 바로가기 만들기
* 바로가기 아이콘을 원하는 위치에 배치해 두고 사용

## 실행하기
* 만일 자동으로 username_key를 프로그램이 추출해 내는데 실패할 경우에는, 수동으로 입력해야 함
* 아래 'username_key 알아내기'를 보고 브라우저에서 알아낸 값을 복사해서 입력하면 됩 (최초 1회만 하면 됨)
* Worlog 기록된 시간을 알고싶은 날짜를 입력하고 엔터 (오늘 날짜를 보고 싶으면 날짜 생략하고 그냥 엔터)
* 입력된 날짜부터 며칠 앞까지 보고싶은지 일수를 입력하고 엔터 (하루치만 보고 싶으면 그냥 엔터)
* 약간 대기후, 출력이 나오면 기존 총 업무시간을 보고 판단할 것
* 원하는 총 업무시간 입력 (원래 시간 그대로 거의 그대로 유지하고 싶으면 그냥 엔터 - 근사치로 계산하기 때문에 몇 분씩 차이가 날 수 있음)
* 맨 마지막 업무시간이 전체 합산 시간임


# 기타 참고사항 (사용방법과 직접 관련 없음)

## username_key 알아내기
* Jira에 로그인된 브라우저에서,
* 아래와 같이 맨 끝 내용에 자신의 이메일 주소를 넣은후, 브라우저 URL로 넣어서 나오는 정보를 확인한다.
```
https://higen-rnd.atlassian.net/rest/api/2/user/search?query=dhkima@higenrnm.com
```
* 내용 중에 `accountId`에 해당하는 내용을 복사해서, 잘 보관해 둔다.

## 특정 Issue 내용 추출하기
* Jira에 로그인된 브라우저에서,
* 아래와 같이 맨 끝 내용에 이슈 번호를 넣은후, 브라우저 URL로 넣어서 나오는 정보를 확인한다.
```
https://higen-rnd.atlassian.net/rest/api/2/issue/A10ETC-28
```
