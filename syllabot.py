```python
import asyncio
import threading
import json
import time
import os
import shutil
import pypdf
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from docx import Document
from strands import Agent, tool
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import discord

BASE_DIR = r"C:\Users\ahipu\Documents\Syllabot_Courses"
INBOX_FOLDER = os.path.join(BASE_DIR, "Inbox")
DISCORD_BOT_TOKEN = os.environ.get("SYLLABOT_DISCORD_BOT_TOKEN")
DISCORD_USER_ID = int(
    os.environ.get(
        "SYLLABOT_DISCORD_USER_ID",
        "0"
    )
)
DISCORD_CHANNEL_ID = int(
    os.environ.get(
        "SYLLABOT_DISCORD_CHANNEL_ID",
        "0"
    )
)

GOOGLE_CREDENTIALS_FILE = os.path.join(
    os.path.dirname(__file__),
    "credentials.json"
)

GOOGLE_TOKEN_FILE = os.path.join(
    os.path.dirname(__file__),
    "token.json"
)

GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]

intents = discord.Intents.default()
intents.message_content = True

discord_client = discord.Client(
    intents=intents
)

discord_ready = threading.Event()
discord_response_event = threading.Event()
discord_response = None
discord_response_lock = threading.Lock()
discord_loop = None


@discord_client.event
async def on_ready():
    print(
        f"[Discord] Logged in as {discord_client.user}"
    )

    discord_ready.set()


@discord_client.event
async def on_message(message):
    global discord_response

    if message.author.bot:
        return

    if message.author.id != DISCORD_USER_ID:
        print("[Discord DEBUG] Ignored because wrong user.")
        return

    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    response = message.content.strip().lower()

    print(
        f"[Discord DEBUG] Response = {repr(response)}"
    )

    if response not in ("y", "n"):
        print("[Discord DEBUG] Ignored because not Y/N.")
        return

    with discord_response_lock:
        discord_response = response

    print(
        f"[Discord DEBUG] ACCEPTED: {response.upper()}"
    )

    discord_response_event.set()

    print("[Discord DEBUG]")


async def send_discord_message(message_text):
    channel = discord_client.get_channel(
        DISCORD_CHANNEL_ID
    )

    if channel is None:
        channel = await discord_client.fetch_channel(
            DISCORD_CHANNEL_ID
        )

    await channel.send(message_text)


def run_discord_bot():
    global discord_loop

    discord_loop = asyncio.new_event_loop()

    asyncio.set_event_loop(discord_loop)

    try:
        discord_loop.run_until_complete(
            discord_client.start(
                DISCORD_BOT_TOKEN
            )
        )

    except Exception as e:
        print(
            f"[Discord Error] Bot stopped: {e}"
        )

    finally:
        discord_loop.close()


def wait_for_discord_response(
    assignment_name,
    due_date,
    due_time,
    timeout=300
):
    global discord_response

    if not discord_ready.wait(timeout=30):
        print(
            "[Discord Error] "
            "Bot did not become ready."
        )

        return "timeout"

    with discord_response_lock:
        discord_response = None

    discord_response_event.clear()

    message_text = (
        "**Syllabot Assignment Detected!**\n\n"
        f"**Assignment:** {assignment_name}\n"
        f"**Due date:** {due_date}\n"
        f"**Due time:** {due_time}\n\n"
        "Add this assignment to Google Calendar?\n"
        "**Reply `Y` or `N`.**"
    )

    try:
        future = asyncio.run_coroutine_threadsafe(
            send_discord_message(message_text),
            discord_loop
        )

        future.result(timeout=10)

    except Exception as e:
        print(
            f"[Discord Error] "
            f"Could not send approval request: {e}"
        )

        return "timeout"

    print(
        "[Discord] Waiting for Y/N response..."
    )

    response_received = discord_response_event.wait(
        timeout=timeout
    )

    if not response_received:
        print(
            "[Discord] No response received "
            "within 5 minutes."
        )

        return "timeout"

    with discord_response_lock:
        response = discord_response

    return response


def get_calendar_service():

    credentials = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(
            GOOGLE_TOKEN_FILE,
            GOOGLE_CALENDAR_SCOPES
        )

    if not credentials or not credentials.valid:

        if (
            credentials
            and credentials.expired
            and credentials.refresh_token
        ):
            credentials.refresh(
                Request()
            )

        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_FILE,
                GOOGLE_CALENDAR_SCOPES
            )

            credentials = flow.run_local_server(
                port=0
            )

        with open(
            GOOGLE_TOKEN_FILE,
            "w",
            encoding="utf-8"
        ) as token_file:
            token_file.write(
                credentials.to_json()
            )

    service = build(
        "calendar",
        "v3",
        credentials=credentials
    )

    return service


@tool
def add_to_calendar(
    assignment_name: str,
    due_date: str,
    due_time: str = "23:59"
) -> str:

    try:
        service = get_calendar_service()

        start_datetime = f"{due_date}T{due_time}:00"

        event = {
            "summary": assignment_name,
            "start": {
                "dateTime": start_datetime,
                "timeZone": "America/Toronto"
            },
            "end": {
                "dateTime": start_datetime,
                "timeZone": "America/Toronto"
            },
            "description": (
                "Assignment detected and added "
                "by Syllabot."
            )
        }

        created_event = service.events().insert(
            calendarId="primary",
            body=event
        ).execute()

        event_link = created_event.get(
            "htmlLink",
            ""
        )

        print(
            f"[Success] Added to Google Calendar: "
            f"{assignment_name}"
        )

        return (
            f"Successfully added '{assignment_name}' "
            f"to Google Calendar. "
            f"{event_link}"
        )

    except Exception as e:

        print(
            f"[Error] Could not add assignment "
            f"to Google Calendar: {e}"
        )

        return (
            f"Failed to add assignment to "
            f"Google Calendar due to error: {e}"
        )


@tool
def read_file(file_path: str) -> str:

    try:
        time.sleep(0.5)

        extension = os.path.splitext(
            file_path
        )[1].lower()

        if extension == ".pdf":
            reader = pypdf.PdfReader(file_path)
            content = ""

            for page in reader.pages:
                text = page.extract_text()

                if text:
                    content += text + "\n"

            print(
                f"[Success] Read PDF: {file_path}"
            )

            if content.strip():
                return content

            return "[The PDF contained no extractable text.]"

        elif extension == ".docx":
            document = Document(file_path)

            content = "\n".join(
                paragraph.text
                for paragraph in document.paragraphs
                if paragraph.text.strip()
            )

            print(
                f"[Success] Read DOCX: {file_path}"
            )

            if content.strip():
                return content

            return "[The DOCX file was empty.]"

        elif extension == ".txt":
            with open(
                file_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as file:
                content = file.read()

            print(
                f"[Success] Read TXT: {file_path}"
            )

            if content.strip():
                return content

            return "[The text file was empty.]"

        else:
            return f"[Unsupported file type: {extension}]"

    except Exception as e:
        print(
            f"[Error] Could not read file: {e}"
        )

        return f"Failed to read file due to error: {e}"


@tool
def put_in_folder(
    coursename: str,
    coursecode: str,
    category: str,
    file_path: str
) -> str:

    try:
        folder_name = f"{coursecode}_{coursename}"

        target_dir = os.path.join(
            BASE_DIR,
            folder_name,
            category
        )

        os.makedirs(
            target_dir,
            exist_ok=True
        )

        file_name = os.path.basename(file_path)

        destination_path = os.path.join(
            target_dir,
            file_name
        )

        if os.path.exists(destination_path):
            name, extension = os.path.splitext(file_name)

            timestamp = int(time.time())

            destination_path = os.path.join(
                target_dir,
                f"{name}_{timestamp}{extension}"
            )

        shutil.move(
            file_path,
            destination_path
        )

        print(
            f"[Success] Moved {file_name} "
            f"into {target_dir}"
        )

        return (
            f"Successfully moved file to "
            f"{destination_path}"
        )

    except Exception as e:

        print(
            f"[Error] Could not move file: {e}"
        )

        return (
            f"Failed to move file due to error: {e}"
        )


agent = Agent(
    system_prompt="""
You are Syllabot, an autonomous academic
file-management agent.

When given a new academic file:

1. Use read_file to read the file.

2. Determine:
   - course name
   - course code
   - category

3. Categories may include:
   - Assignment
   - Lecture
   - Lab
   - Notes
   - Syllabus
   - Exam
   - Other

4. Do NOT invent information.
   Only use information that can reasonably
   be determined from the file.

5. Use put_in_folder to move the file into
   the appropriate course/category folder.

6. Determine whether the file is an assignment.

7. If the file is an assignment:
   - Determine the assignment name.
   - Determine the due date if one is stated.
   - Determine the due time if one is stated.
   - If the due date is not stated, say so.
   - If the due time is not stated, say so.
   - Never guess a missing due date or time.

8. At the end of your response, provide the
   assignment information using exactly this format:

ASSIGNMENT: YES or NO
ASSIGNMENT_NAME: <name or NONE>
DUE_DATE: <YYYY-MM-DD or NONE>
DUE_TIME: <HH:MM or NONE>

9. Report what you determined and what actions
   you took.

Be careful with course codes, assignment names,
and due dates.

If information is ambiguous, say that it is
ambiguous rather than making up an answer.
""",
    tools=[
        read_file,
        put_in_folder
    ]
)


def wait_for_file(file_path, timeout=30):

    start_time = time.time()
    previous_size = -1

    while time.time() - start_time < timeout:

        if not os.path.exists(file_path):
            time.sleep(0.5)
            continue

        try:
            current_size = os.path.getsize(
                file_path
            )

        except OSError:
            time.sleep(0.5)
            continue

        if current_size == previous_size:
            return True

        previous_size = current_size

        time.sleep(0.5)

    return False


def process_file(file_path):

    if not os.path.isfile(file_path):
        return

    extension = os.path.splitext(
        file_path
    )[1].lower()

    if extension not in (
        ".pdf",
        ".txt",
        ".docx"
    ):
        return

    print()
    print("=" * 60)
    print(
        f"[New academic file detected]\n"
        f"{file_path}"
    )
    print("=" * 60)

    if not wait_for_file(file_path):
        print(
            "[Error] File did not become stable "
            "within the timeout."
        )
        return

    print(
        "[Ready] File is stable. "
        "Sending to Syllabot..."
    )

    message = f"""
A new academic file has arrived:

{file_path}

Process this file according to your instructions.

Read the file first.

Determine:
- course name
- course code
- category
- whether it is an assignment
- assignment name, if applicable
- due date, if stated
- due time, if stated

Then move the file to the appropriate folder.

If it is an assignment, Python will ask the user for calendar approval through Discord.

At the end of your response, provide:

ASSIGNMENT: YES or NO
ASSIGNMENT_NAME: <name or NONE>
DUE_DATE: <YYYY-MM-DD or NONE>
DUE_TIME: <HH:MM or NONE>
"""

    try:
        result = agent(message)

        print()
        print("[Agent Result]")
        print(result)

        result_text = str(result)

        assignment = "NO"
        assignment_name = "NONE"
        due_date = "NONE"
        due_time = "NONE"

        for line in result_text.splitlines():

            line = line.strip()

            if line.startswith("ASSIGNMENT:"):
                assignment = line.split(
                    ":",
                    1
                )[1].strip().upper()

            elif line.startswith("ASSIGNMENT_NAME:"):
                assignment_name = line.split(
                    ":",
                    1
                )[1].strip()

            elif line.startswith("DUE_DATE:"):
                due_date = line.split(
                    ":",
                    1
                )[1].strip()

            elif line.startswith("DUE_TIME:"):
                due_time = line.split(
                    ":",
                    1
                )[1].strip()

        if assignment != "YES":
            print(
                "[Calendar] No assignment detected. "
                "Nothing to add."
            )
            return

        if assignment_name == "NONE":
            print(
                "[Calendar] Assignment detected, "
                "but no assignment name was found."
            )
            return

        print()
        print("=" * 60)
        print(" ASSIGNMENT DETECTED")
        print("=" * 60)

        print(
            f"Assignment: {assignment_name}"
        )

        print(
            f"Due date:   {due_date}"
        )

        print(
            f"Due time:   {due_time}"
        )

        print("=" * 60)

        if due_date == "NONE":
            print(
                "[Calendar] No due date was found. "
                "Calendar event will not be created."
            )
            return

        calendar_time = (
            "23:59"
            if due_time == "NONE"
            else due_time
        )

        approval = wait_for_discord_response(
            assignment_name=assignment_name,
            due_date=due_date,
            due_time=due_time,
            timeout=300
        ).strip().lower()

        if approval != "y":
            print(
                "[Calendar] User declined. "
                "Nothing was added."
            )
            return

        print(
            "[Calendar] Adding assignment "
            "to Google Calendar..."
        )

        calendar_result = add_to_calendar(
            assignment_name=assignment_name,
            due_date=due_date,
            due_time=calendar_time
        )

        print(
            f"[Calendar Result]\n"
            f"{calendar_result}"
        )

        try:
            future = asyncio.run_coroutine_threadsafe(
                send_discord_message(
                    f" **Syllabot Calendar Result**\n\n"
                    f"{calendar_result}"
                ),
                discord_loop
            )

            future.result(timeout=10)

        except Exception as e:
            print(
                f"[Discord Error] "
                f"Could not send calendar result: {e}"
            )

    except Exception as e:
        print(
            f"[Error] Agent execution failed: {e}"
        )


class SyllabotHandler(FileSystemEventHandler):

    def on_created(self, event):

        if event.is_directory:
            return

        file_path = event.src_path

        extension = os.path.splitext(
            file_path
        )[1].lower()

        if extension in (
            ".tmp",
            ".crdownload",
            ".part",
            ".tmp~"
        ):
            print(
                f"[Temporary download detected] "
                f"{os.path.basename(file_path)}"
            )
            return

        process_file(file_path)

    def on_moved(self, event):

        if event.is_directory:
            return

        file_path = event.dest_path

        print(
            f"[File moved/renamed] "
            f"{os.path.basename(file_path)}"
        )

        process_file(file_path)


if __name__ == "__main__":

    if not DISCORD_BOT_TOKEN:

        print(
            "[Error] Discord bot token is not configured."
        )

        raise SystemExit(1)

    discord_thread = threading.Thread(
        target=run_discord_bot,
        daemon=True
    )

    discord_thread.start()

    if not discord_ready.wait(timeout=30):

        print(
            "[Error] Discord bot did not become ready "
            "within 30 seconds."
        )

        raise SystemExit(1)

    os.makedirs(
        INBOX_FOLDER,
        exist_ok=True
    )

    event_handler = SyllabotHandler()

    observer = Observer()

    observer.schedule(
        event_handler,
        path=INBOX_FOLDER,
        recursive=False
    )

    observer.start()

    print()
    print("=" * 60)
    print("Syllabot is LIVE")
    print("=" * 60)

    print(
        f"Watching:\n{INBOX_FOLDER}"
    )

    print()

    print(
        "Drop a PDF, DOCX, or TXT file "
        "into Inbox to test it."
    )

    print(
        "Press Ctrl+C to stop."
    )

    print("=" * 60)

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print(
            "\nStopping Syllabot..."
        )

        observer.stop()

    observer.join()
```
