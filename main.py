
import uuid
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pdf2docx import Converter

# 项目目录
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
UPLOAD_TTL = 30 * 60              # PDF 保留 30 分钟
OUTPUT_TTL = 60 * 60              # Word 保留 1 小时

app = FastAPI(title="文档快转 API")

# 提供 Word 文件下载
app.mount(
    "/files",
    StaticFiles(directory=str(OUTPUT_DIR)),
    name="files"
)


def cleanup_directory(directory: Path, ttl: int):
    """删除超过保留时间的文件"""
    now = time.time()

    for file_path in directory.iterdir():
        if not file_path.is_file():
            continue

        try:
            if now - file_path.stat().st_mtime > ttl:
                file_path.unlink()
                print(f"已清理过期文件：{file_path.name}")
        except OSError as error:
            print(f"清理文件失败：{error}")


def cleanup_expired_files():
    cleanup_directory(UPLOAD_DIR, UPLOAD_TTL)
    cleanup_directory(OUTPUT_DIR, OUTPUT_TTL)


@app.middleware("http")
async def auto_cleanup(request: Request, call_next):
    # 每次收到请求时检查过期文件
    cleanup_expired_files()
    return await call_next(request)


@app.get("/")
def home():
    return {"message": "文档快转服务运行正常"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/convert")
async def convert_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件")

    file_id = uuid.uuid4().hex
    pdf_path = UPLOAD_DIR / f"{file_id}.pdf"
    docx_path = OUTPUT_DIR / f"{file_id}.docx"

    converter = None

    try:
        # 保存上传文件，并检查大小和 PDF 文件头
        size = 0
        first_chunk = True

        with pdf_path.open("wb") as saved_file:
            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                size += len(chunk)

                if size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="文件不能超过 20 MB"
                    )

                if first_chunk:
                    if not chunk.startswith(b"%PDF-"):
                        raise HTTPException(
                            status_code=400,
                            detail="文件内容不是有效的 PDF"
                        )
                    first_chunk = False

                saved_file.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="文件为空")

        # PDF 转 Word
        converter = Converter(str(pdf_path))
        converter.convert(str(docx_path))

        if not docx_path.exists() or docx_path.stat().st_size == 0:
            raise RuntimeError("没有生成有效的 Word 文件")

        return {
            "success": True,
            "message": "转换成功",
            "download_url": (
                f"http://127.0.0.1:8000/files/{file_id}.docx"
            )
        }

    except HTTPException:
        raise

    except Exception as error:
        print(f"PDF 转换失败：{error}")
        raise HTTPException(
            status_code=500,
            detail="转换失败，请检查 PDF 文件后重试"
        )

    finally:
        if converter is not None:
            converter.close()

        # 无论转换成功或失败，都删除上传的 PDF
        if pdf_path.exists():
            pdf_path.unlink()

        # 转换失败时删除不完整的 Word 文件
        if docx_path.exists():
            if docx_path.stat().st_size == 0:
                docx_path.unlink()

        await file.close()
