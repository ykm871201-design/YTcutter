# 외부 구성요소 및 라이선스 고지

YT Cutter 는 아래 오픈소스 구성요소를 사용합니다. 실행 파일을 **다른 사람에게 배포할 때**는
이 파일과 각 라이선스 전문을 함께 넣어 주세요. (아래 내용은 법률 자문이 아닙니다. 상업적 배포 전에는 각 라이선스 원문을 확인하세요.)

| 구성요소 | 용도 | 라이선스 | 배포 시 유의사항 |
|---|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 영상 정보 조회·다운로드 | Unlicense (퍼블릭 도메인) | 제약 거의 없음. 고지 권장 |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | 유튜브 JS 처리 스크립트 | Unlicense (포함된 JS 의존성은 MIT/ISC 등) | 패키지에 포함된 라이선스 파일 유지 |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) / Qt 6 | GUI, 영상 재생 | LGPL v3 (또는 상용) | ① LGPL 전문 동봉 ② 사용자가 Qt 라이브러리를 교체할 수 있도록 **동적 링크 유지**(PyInstaller onedir 방식은 DLL이 분리되어 있어 충족) ③ 요청 시 Qt 소스 제공 방법 안내 |
| Qt Multimedia 내장 FFmpeg | 앱 안의 영상 재생 | LGPL v2.1+ | PySide6 휠에 포함. 위 Qt 고지와 함께 유지 |
| [FFmpeg](https://ffmpeg.org/legal.html) (별도 실행 파일) | 병합·컷·인코딩 | 빌드에 따라 LGPL v2.1+ 또는 **GPL v2/v3** | 정확한 컷에 쓰는 libx264 가 포함된 빌드(예: gyan.dev essentials)는 **GPL**. ffmpeg.exe 를 동봉해 배포하면 FFmpeg 라이선스 전문과 **소스 코드 입수 방법**(빌드 제공처 링크 또는 소스 사본)을 함께 제공해야 함. 본 앱은 FFmpeg 를 별도 프로세스로 실행하므로 앱 코드 자체에 GPL 이 전파되지는 않는 것으로 일반적으로 해석됨 |
| x264 (FFmpeg 에 포함) | H.264 인코딩 | GPL v2+ | 위 FFmpeg 항목과 동일. H.264 특허(MPEG LA/Via LA) 관련 사항은 국가·용도별로 확인 |
| [Deno](https://deno.com) (선택) | 유튜브 JS 처리 런타임 | MIT | 동봉 시 MIT 라이선스 고지 |
| Python | 실행 환경 | PSF License | PyInstaller 로 묶인 경우 고지 권장 |
| PyInstaller (빌드 도구) | exe 패키징 | GPL v2 + 부트로더 예외 | 부트로더 예외로 결과물 배포에 제약 없음 |

## 이용 책임

- 이 프로그램은 **본인이 소유했거나 다운로드·편집 권한을 받은 영상**에 사용하도록 만들어졌습니다.
- YouTube 서비스 약관은 YouTube 가 제공하는 기능 외의 방법으로 콘텐츠를 내려받는 것을 제한할 수 있습니다. 약관과 저작권법을 지킬 책임은 사용자에게 있습니다.
- DRM·유료 콘텐츠·로그인 필요 콘텐츠 등 접근 제한을 우회하는 기능은 구현되어 있지 않으며, 추가하지 마세요.
