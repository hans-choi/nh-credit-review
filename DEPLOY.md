# NH 여신심사 AI POC — Render 배포 가이드

NPP 배포 (`npp-contract-manager.onrender.com`) 와 동일한 구성:
- **Render Starter plan** (월 $7, 15분 idle 후 스핀다운 없음, 1GB 영구 디스크)
- **Docker 빌드** (`Dockerfile` 사용)
- **영구 디스크 `/data`** — 업로드/규정/케이스 모두 유지
- **최초 부팅 시 시드 자동 복사** — 지금까지 쌓인 124개 업로드 + 규정/케이스/로그 그대로 주입

---

## 1. 배포 전 준비

### 1.1 시드 번들 생성 (이미 완료됨)

로컬 `backend/data/` 와 `backend/uploads/` 를 배포용으로 슬림화해서 `backend/seed/` 에 저장.

```bash
./venv/bin/python tools/prepare_deploy_bundle.py
# → backend/seed/ ≈ 65MB
```

- `documents.json` 443MB → 12.6MB (base64 thumbnail 제거, 온디맨드 재생성)
- `uploads/` 124개 실제 업로드 PDF/이미지 포함

**데이터를 더 업로드하거나 새 심사건을 돌린 후에는 재실행하세요**:
```bash
./venv/bin/python tools/prepare_deploy_bundle.py
git add backend/seed/
git commit -m "update seed bundle"
git push
```

### 1.2 Upstage API 키

Render 대시보드에서 환경변수로 설정 (코드에 하드코딩 금지).

---

## 2. GitHub 리포 생성 + 푸시

```bash
cd /Users/kakao_ent1/Downloads/nh-credit-review

git init
git add .
git commit -m "Initial commit — NH credit review POC"

# GitHub 리포 생성 후
git branch -M main
git remote add origin https://github.com/<you>/nh-credit-review.git
git push -u origin main
```

**체크**: `.gitignore` 덕분에 `backend/data/` 443MB, `backend/uploads/` 48MB 는 제외.
`backend/seed/` (65MB) 만 커밋됨 → Render 빌드 시 이미지에 베이크.

---

## 3. Render 배포

1. **https://dashboard.render.com** → **New +** → **Blueprint**
2. GitHub 리포 연결 → `render.yaml` 자동 감지
3. 서비스 이름 확인: `nh-credit-review`
4. 배포 시작 전 **Environment** 탭에서 필수 env 세팅:
   - `UPSTAGE_API_KEY` = `up_...`  (Upstage 콘솔에서 복사)
5. **Apply** → 빌드 시작 (약 5-8분)

배포 후 URL: `https://nh-credit-review.onrender.com`

---

## 4. 시드 데이터 확인

첫 부팅 시 entrypoint 가 자동으로 `/app/seed/*` → `/data/*` 복사.

로그에서 확인:
```
[seed] First boot — populating persistent disk from /app/seed
[seed] Copied uploads/ (124 files)
[seed]   + documents.json
[seed]   + extractions.json
[seed]   + regulations.json
[seed]   + review_cases.json
[seed]   + usage_logs.json
[seed] Done.
```

이후 재배포는 `[seed] Already seeded — skip.` 로 건너뜀 → 사용자 데이터 손실 없음.

---

## 5. 커스텀 도메인 (선택)

Render → Settings → Custom Domains → 추가. CNAME 설정 필요.

---

## 6. 트러블슈팅

### Document Parse 호출 실패
`UPSTAGE_API_KEY` 확인. `/api/health` 응답에서 `has_upstage_key: true` 확인.

### 페이지 썸네일이 안 보임
시드 데이터는 base64 page_images 가 비어있음. 상세 뷰 진입 시 자동으로 `/api/documents/{id}/render-pages` 가 PyMuPDF 로 재생성함 → 정상.

### 영구 디스크 재시드 강제
```bash
# Render Shell
rm /data/store/.seeded && touch /tmp/restart.trigger
# 또는 Render 대시보드에서 Manual Deploy
```

### 디스크 용량 부족
현재 1GB. 부족 시 `render.yaml` → `sizeGB: 2` 로 수정 후 커밋/푸시.

---

## 7. 운영 후 백업

로컬로 persistent disk 내용 가져오기 (Render Shell 에서):

```bash
tar czf /tmp/backup.tar.gz /data
# SCP 다운로드 (Render starter plan 지원)
```
