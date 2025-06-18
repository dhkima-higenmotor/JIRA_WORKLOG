
# 개요

# 실행 조건
* 파이썬 패키지 관리자 uv가 설치되어 있을 것

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
* Worlog 기록된 시간을 알고싶은 날짜를 입력하고 엔터
* 그 날짜의 제한하고 싶은 총 업무시간을 입력하고 엔터 (기존에 입력되어 있던 업무시간을 유지하고 싶으면 0 치고 엔터)
* 맨 마지막 업무시간이 전체 합산 시간임


# 기타 참고사항

## username_key 알아내기
* Jira에 로그인된 브라우저에서,
* 아래와 같이 맨 끝 내용에 자신의 이메일 주소를 넣은후, 브라우저 URL로 넣어서 나오는 정보를 확인한다.
```
https://higen-rnd.atlassian.net/rest/api/2/user/search?query=dhkima@higenrnm.com
```
* 내용 중에 `accountId`에 해당하는 내용을 복사해서, 잘 보관해 둔다.

## 특정 Issue 내용 추출하기
* Jira에 로그인된 브라우저에서,
* 아래와 같이 맨 끝 내용에 이슈 번호호를 넣은후, 브라우저 URL로 넣어서 나오는 정보를 확인한다.
```
https://higen-rnd.atlassian.net/rest/api/2/issue/A10ETC-28
```
