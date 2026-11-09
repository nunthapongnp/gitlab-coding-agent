# Coding Agent --- Phase 0 ภาพรวมการออกแบบระบบ

> เอกสารสรุปภาพรวมขอบเขต สถาปัตยกรรม และข้อตกลงการออกแบบ Coding Agent สำหรับ
> Phase 0\
> สถานะ: Design Summary\
> เทคโนโลยีหลัก: LangGraph, OpenHands SDK, Ollama/LLM Model และ GitLab

## 1. วัตถุประสงค์

พัฒนา Coding Agent ที่รับงานจาก GitLab Issue สามารถวิเคราะห์ Requirement และ
Repository เพื่อสร้าง Execution Plan ให้ Developer ตรวจสอบและอนุมัติ
ก่อนนำแผนที่อนุมัติแล้วไปแก้ไขโค้ด ตรวจสอบ และส่งการเปลี่ยนแปลงกลับไปยัง GitLab Merge
Request (MR)

ระบบใช้การรัน python script เป็นช่องทางทำงานใน Phase 0 โดยคงรูปแบบคำสั่งเดิม และแยกการทำงานเป็น
Plan Mode และ Execute Mode อย่างชัดเจน

## 2. Technology Stack

-   **LangGraph:** ควบคุม Workflow, State, Routing และการประสานงานระหว่าง
    Agents
-   **OpenHands SDK:** เป็นเครื่องมือให้ Coding Agent ตรวจสอบและแก้ไขโค้ดใน
    Workspace
-   **Ollama + LLM Model:** ให้บริการ LLM สำหรับ Planner, Coding Agent และ
    Reviewer
-   **GitLab API:** อ่าน Issue, Comments, Labels, Repository และ MR
    รวมถึงเผยแพร่ Plan และ Execution Report
-   **Python CLI:** เป็น Interface หลักสำหรับเรียกใช้งานระบบ

## 3. ขอบเขต Phase 0

-   ทำงานเฉพาะ Repository ของ GitLab Project ที่ระบุใน Issue เท่านั้น
-   รองรับ Repository ที่มีทั้ง Web และ API อยู่ใน Repository เดียวกัน
-   รองรับ Repository ที่ไม่มี `AGENTS.md`
-   วิเคราะห์ Source Code, Configuration และ CI ภายใน Repository ปัจจุบัน
-   ไม่ทำ Multi-repository และไม่ค้นหา แก้ไข หรือ Clone Repository อื่น
-   ไม่ทำงานกับ Deployment Project และไม่สั่ง Deploy
-   ไม่เชื่อมต่อหรือแก้ไขฐานข้อมูลจริง และไม่รัน Migration กับ Environment จริง
-   ไม่ทำ Integration Test หรือ E2E Test ใน Phase 0
-   ไม่ทำ Web UI หรือ API สำหรับผู้ใช้ใน Phase 0

## 4. โหมดการทำงาน

### 4.1 Plan Mode

ต้องเป็น Read-only อย่างแท้จริง

ความสามารถหลัก: - อ่าน GitLab Issue และ Comments ทั้งหมด - ตรวจสอบ
Repository, Source Code, Configuration และ CI - วิเคราะห์ Requirement
และผลกระทบภายใน Repository ปัจจุบัน - ค้นหา Build และ Unit Test Commands
ที่มีอยู่จริง - แบ่งงานเป็น Tasks พร้อม Dependencies, Scope และ Acceptance
Criteria - สร้าง ตรวจสอบ และจัด Version ของ Execution Plan - เผยแพร่ Plan
กลับไปยัง Issue ผ่าน Comment และจัดการ Labels

ข้อห้าม: - ไม่แก้ไข Source Code หรือไฟล์ใด ๆ - ไม่สร้างหรือแก้ไข Branch - ไม่
Commit หรือ Push - ไม่สร้าง MR

Plan สามารถนำไปใช้เป็นแนวทางให้ Developer ลงมือแก้ไขเองได้ แม้ไม่ได้ใช้ Execute
Mode

### 4.2 Plan Feedback และ Versioning

-   Developer ให้ Feedback ด้วยภาษาปกติผ่าน Issue Comments ได้
-   เมื่อรัน Plan ใหม่ ระบบต้องอ่าน Comments ทั้งหมดและแยก Feedback ใหม่ เก่า
    จัดการแล้ว หรือขัดแย้งกัน
-   วิเคราะห์ว่างานส่วนใดของ Plan เปลี่ยนและเพราะเหตุใด ก่อนเผยแพร่ Plan Version
    ใหม่
-   ปรับเฉพาะส่วนที่ได้รับผลกระทบเมื่อทำได้
-   Plan Comment History เป็นแบบ Append-only โดยแต่ละ Version สร้าง Comment
    ใหม่
-   หาก Plan เปลี่ยนหลัง Approval ต้องถอด `agent::approved` และต้องอนุมัติ
    Version ใหม่อีกครั้ง

### 4.3 Execute Mode

ต้องดำเนินการตาม Approved Plan เป็นหลักไม่คิด Scope ใหม่เอง

เงื่อนไขสำคัญ: - Issue ต้องมี `agent::approved` ที่ผูกกับ Plan Version ล่าสุด -
Developer เป็นผู้สร้าง Source Branch และ MR เอง - Agent ค้นหา MR ที่เชื่อมโยงกับ
Issue และใช้ Source Branch ของ MR เป็น Branch สำหรับทำงาน - หากไม่มี MR
ให้หยุดและแจ้งให้ผู้ใช้สร้าง MR - หากมี Open MR มากกว่าหนึ่งรายการ
ให้หยุดและขอให้ผู้ใช้ระบุ MR - MR ต้องอยู่ใน Project เดียวกับ Issue และยังเปิดอยู่ -
Agent ไม่สร้าง Branch ใหม่ และไม่เปลี่ยน Target Branch - ก่อนเริ่มต้องตรวจ Git
Status และ Diff; หากมี Uncommitted Changes ให้หยุดโดยไม่ Stash หรือแก้ไข - หาก
Remote Branch มี Commit ใหม่ที่ Local ไม่มี ให้หยุดและแจ้งผู้ใช้ ห้าม Pull, Rebase
หรือ Merge อัตโนมัติ

## 5. Branch Selection

**Plan Mode** - ไม่มี MR ที่เชื่อมโยง: ใช้ Project Default Branch - มี MR
เพียงหนึ่งรายการ: ใช้ MR Target Branch เป็น Baseline - มีหลาย MR แต่มี Open MR
เพียงหนึ่งรายการ: ใช้ Target Branch ของ Open MR - มี Open MR มากกว่าหนึ่งรายการ:
ต้องระบุ `--branch` - ผู้ใช้สามารถระบุ `--branch` เพื่อ Override ได้

**Execute Mode** - ใช้ Source Branch ของ MR ที่เชื่อมโยงกับ Issue - ไม่สร้าง
Branch และไม่เปลี่ยน Target Branch

## 6. Agent Roles

-   **Coordinator:** ควบคุม Workflow, Task Dependencies, File Ownership,
    Parallel Execution, Retry และสถานะงานผ่าน LangGraph
-   **Planner Agent:** วิเคราะห์ Requirement, Feedback และ Repository
    เพื่อสร้าง Plan โดยทำงานแบบ Read-only
-   **Coding Agent:** ใช้ OpenHands SDK แก้ไขเฉพาะ Tasks ที่ได้รับอนุมัติ
-   **Reviewer Agent:** ตรวจ Diff, Acceptance Criteria และ Scope ก่อน
    Commit; ให้ Feedback ได้แต่ไม่มีสิทธิ์แก้โค้ด
-   **Verification:** รัน Unit Tests และ Build ด้วยคำสั่งจริงที่ค้นพบจาก
    Repository
-   **GitLab Integration Service:** ติดต่อ GitLab API ไม่ใช่ LLM Agent

Reviewer เป็น Agent/Context แยกจาก Coding Agent และสามารถใช้ Gemma Model
เดียวกันได้

## 7. Task Execution และ Parallelism

-   แบ่งงานเป็น Tasks ขนาดเล็ก มี Scope และ Acceptance Criteria ชัดเจน
-   ทำ Tasks ที่ไม่มี Dependency ต่อกันแบบขนานได้ สูงสุด 3 Coding Agents
-   Tasks ที่แก้ไขไฟล์ร่วมกันต้องจัดการ File Ownership และทำงานแบบไม่ชนกัน
-   Coding Agents ใช้ Shared Workspace และ Source Branch เดียวกัน
-   Task ต้องผ่าน Reviewer ก่อน Commit
-   เมื่อ Reviewer ผ่าน ให้ Commit Task นั้นทันที ไม่ต้องรอ Task อื่นในกลุ่ม Parallel
-   หาก Task ล้มเหลว Tasks อิสระยังทำต่อได้ ส่วน Tasks ที่ขึ้นต่อกันให้เป็น Blocked

สถานะระหว่างทำงานประกอบด้วย `PENDING`, `IN_PROGRESS`, `REVIEW`,
`REVISION`, `COMPLETED` และ `BLOCKED` ส่วนสถานะใน Execution Report ใช้
`COMPLETED`, `FAILED`, `BLOCKED` และ `PENDING`

## 8. Review, Retry และ Verification

-   Reviewer ตรวจทุก Task ก่อน Commit
-   หาก Review ไม่ผ่าน ส่งกลับ Coding Agent เพื่อแก้ไข
-   Reviewer Retry สูงสุด 3 ครั้งต่อ Task; หากยังไม่ผ่านให้ Task เป็น Failed
-   หลัง Tasks เสร็จ ให้รัน Repository-level Unit Tests และ Build
-   Unit Test ใช้เมื่อ Repository มีคำสั่งหรือ Framework อยู่แล้ว หากไม่มีให้เป็น
    `SKIPPED` และไม่สร้าง Test Infrastructure ใหม่
-   Build เป็น Mandatory Verification
-   หากไม่พบ Build Command ให้รายงาน `UNVERIFIED`; หากติดข้อจำกัด Environment
    ให้เป็น `BLOCKED`
-   Test Failure ที่เกิดจาก Code Regression ให้แก้ไข; Failure
    เดิมที่ไม่เกี่ยวข้องให้บันทึกไว้และไม่แก้เกิน Scope; Environment Failure ให้เป็น
    `BLOCKED`
-   Test Failure Retry สูงสุด 3 ครั้งต่อ Task แยกจาก Reviewer Retry
-   Build/Test Retry สูงสุด 3 ครั้ง
-   หาก Verification ยังไม่ผ่านหลัง Retry ให้ Push การเปลี่ยนแปลงและรายงานผลเป็น
    Failure โดยห้ามกล่าวอ้างว่าสำเร็จ

ผล Verification ใช้ `PASS`, `FAIL`, `SKIPPED`, `BLOCKED` และ `UNVERIFIED`

## 9. Git และ Commit Strategy

-   Agent ทำงานบน Source Branch ที่ผู้ใช้สร้างไว้แล้ว
-   เก็บรักษา User Commits เดิม ห้าม Reset, Force Push, Squash หรือ Rebase
-   Commit แยกต่อ Task หลัง Reviewer ผ่าน
-   รูปแบบ Commit Message: `agent(#123-P2-R001-T001): support SKU search`
-   Push หลังหลาย Tasks ได้ตามความเหมาะสม แต่ Push Retry สูงสุด 3 ครั้ง
-   ห้ามสร้าง Duplicate Commit จากการ Retry Push
-   หากมีการเปลี่ยนแปลงจากผู้ใช้ระหว่างทำงาน ให้หยุด Task ที่ได้รับผลกระทบ
-   ห้าม Commit Unrelated User Changes

## 10. Scope Guardrails

-   แก้ไข Java, TypeScript, HTML, CSS และ Unit Tests ได้เฉพาะภายใน
    Approved Tasks
-   ไฟล์ Configuration เช่น Dockerfile, CI Pipeline, `package.json`,
    `pom.xml` และ `application.yml` แก้ได้เมื่อระบุไว้ใน Task เท่านั้น
-   Database Entity, SQL, Migration และ Schema แก้ได้เมื่อ Task
    ระบุชัดเจนเท่านั้น
-   ห้ามแก้ Production Secrets, Certificates, Deployment Project, Server
    Configuration, Ansible และ Terraform ใน Phase 0
-   ห้ามเชื่อมต่อหรือแก้ไข Real Database และห้าม Deploy
-   หากพบความจำเป็นต้องแก้ไขนอก Scope ให้หยุดการแก้ไขและส่งเหตุผลกลับ Coordinator
-   การขยาย Scope ต้องกลับไป Plan Mode สร้าง Plan Version ใหม่ และขอ
    Approval ใหม่

## 11. GitLab Labels

Labels สำหรับ Agent แบ่งตาม Issue และ MR โดย Operational Label ของ Agent
บน Object เดียวกันต้องมีเพียงหนึ่งรายการในแต่ละครั้ง

**Issue** - `agent::plan`: Developer เตรียม Issue สำหรับ Plan -
`agent::in-progress`: Agent กำลังทำ Plan - `agent::needs-review`: Plan
พร้อมให้ Developer ตรวจสอบ - `agent::approved`: Approval State แยกจาก
Operational Status และผูกกับ Plan Version ล่าสุด

**Merge Request** - `agent::implement`: Developer เตรียม MR สำหรับ
Execute - `agent::in-progress`: Agent กำลัง Execute -
`agent::needs-review`: Execute จบแล้วและรอ Developer Review

เมื่อเริ่มงานให้เปลี่ยน Operational Label เป็น `agent::in-progress`;
เมื่อจบให้เปลี่ยนเป็น `agent::needs-review` โดย Execution Report
เป็นตัวบอกว่าสำเร็จหรือล้มเหลว ไม่ใช้ Success/Failure Label แยก

## 12. Execution Report

-   สร้าง Report เพียงหนึ่ง Comment ต่อหนึ่ง Execute Run
-   เป็น Append-only และไม่ Comment แยกทุก Task หรือทุกขั้นตอน
-   Report ประกอบด้วย Run ID, Plan Version, เวลาเริ่ม/จบ, Overall Result,
    Task Summary, รายละเอียด Task, Commit SHAs และ Messages, Unit
    Test/Build Results, Push Result และ Next Action
-   Run ID เช่น `RUN-001` และ Plan Version เช่น `PLAN-v3` เป็นคนละ
    Identifier
-   Overall Result:
    -   `SUCCESS`: Tasks ที่จำเป็นและ Mandatory Verification ผ่านทั้งหมด
    -   `PARTIAL_SUCCESS`: มีบาง Tasks สำเร็จ แต่บาง Tasks Failed หรือ
        Blocked
    -   `FAILED`: งานสำคัญไม่สำเร็จ หรือ Mandatory Verification ไม่ผ่าน
-   เมื่อ Execute จบทุกกรณีให้ MR เป็น `agent::needs-review`

## 13. Execution Budget และ Recovery

ค่าที่กำหนดสำหรับ Phase 0: - OpenHands Maximum Iterations: `60` - OpenHands
Timeout ต่อ Invocation: `1800` วินาที - Verification Timeout: `1200` วินาที -
Execution Maximum Runtime: `7200` วินาที (120 นาที)

การตั้งค่าใช้ Environment Variables ไม่เพิ่ม CLI Option สำหรับ Runtime

ข้อจำกัด Recovery ใน Phase 0: - ไม่มี Persistent Run State หรือ SQLite -
ไม่รองรับ Resume หลัง Process ถูกปิดหรือเครื่อง Restart - ไม่มี Automatic
Workspace Recovery - หาก Process ล้มเหลว ให้รักษา Commits
ที่สร้างไปแล้วไว้ตามเดิม - ห้ามทำ Destructive Cleanup หรือ Reset Workspace
อัตโนมัติ

Advanced Recovery และ Resume ถูกเลื่อนไป Phase 1

## 14. CLI และ Configuration

คงรูปแบบ CLI เดิม:

``` bash
python main.py --project "echo6604438/my-project" --issue 1
```

-   `--branch`: Optional Branch Override สำหรับ Plan Mode
-   ไม่เพิ่ม `--max-runtime`; กำหนด Runtime ผ่าน `.env`

Environment Variables ที่กำหนด:

``` env
OPENHANDS_MAX_ITERATIONS=60
OPENHANDS_TIMEOUT_SECONDS=1800

MAX_FIX_ATTEMPTS=3
CHECK_TIMEOUT_SECONDS=1200

EXECUTION_MAX_RUNTIME_SECONDS=7200
```

หมายเหตุ: ต้องกำหนด Mapping ของ `MAX_FIX_ATTEMPTS` ให้ชัดเจนในรายละเอียด
Implementation เพื่อไม่ให้ซ้ำซ้อนกับ Reviewer, Verification และ Push Retry
Counters ที่แยกกัน

## 15. LangGraph Workflows

**Plan Graph** ประกอบด้วยขั้นตอนหลัก: - Load Issue และ Comments - Load
Repository - Inspect Existing Changes เมื่อ Re-plan - Analyze Requirement
และ Impact - Discover Validation Commands - Reconcile Previous Plan -
Generate และ Validate Plan - Publish Plan - Update Issue Labels

**Execute Graph** ประกอบด้วยขั้นตอนหลัก: - Load Issue และ Plan - Validate
Approval - Find และ Validate MR - Validate Source Branch - Acquire
Execution Lock - Inspect Workspace - Reconcile Task Status - Execute
Tasks - Review และ Commit Tasks - Run Verification และ Retry เมื่อจำเป็น -
Push Changes - Publish Execution Report - Update MR Labels

LangGraph เป็นผู้ควบคุม Workflow ส่วน OpenHands SDK เป็นผู้ดำเนินการแก้ไข Code

## 16. Plan และ GitLab Templates

ระบบมี Template หลัก 5 รายการ: 1. New Issue Template (BA) 2. Issue
Execution Plan Comment Template 3. Issue Plan Feedback Comment Template
4. MR Success Report Comment Template 5. MR Failure Report Comment
Template

ให้ใช้ Template ที่ตกลงไว้เป็น Baseline โดยไม่เปลี่ยนแปลงเนื้อหาโดยพลการ

## 17. Implementation Roadmap

### Phase A --- Foundation & Plan Mode

-   Project Foundation, CLI และ Configuration
-   GitLab Integration
-   Repository Inspection
-   Ollama/Gemma Integration
-   Planner Agent
-   LangGraph Plan Workflow
-   Requirement และ Impact Analysis
-   Task Decomposition และ Dependency
-   Plan Validation และ Versioning
-   Plan Publishing และ Label Management

ผลลัพธ์ที่คาดหวัง: Plan Mode ทำงานได้จริงแบบ Read-only และ Developer สามารถนำ
Plan ไปใช้ได้

### Phase B --- Execute Mode

-   MR และ Approval Validation
-   Workspace และ Branch Inspection
-   OpenHands SDK Integration
-   Coding Agent
-   Task Scheduling และ File Ownership
-   Reviewer Agent
-   Task-level Commit
-   Unit Test และ Build Verification
-   Push Strategy
-   Execution Budget
-   Execution Report และ MR Label Management

ผลลัพธ์ที่คาดหวัง: Execute Mode ทำงานตาม Approved Plan และส่งผลลัพธ์กลับ MR ได้

## 18. Phase 1 --- Future Enhancements

ความสามารถที่ยังไม่อยู่ใน Phase 1: - Persistent Run State - Resume หลัง Process
Restart - Workspace Recovery - Complex Issue Decomposition และ Work
Child Items - RAG to Reduce Time to Explore source code - Deployment Integration -
Integration Test และ E2E Test - Advanced Recovery และ Orchestration

------------------------------------------------------------------------

## 19. ข้อตกลงสำคัญในการ Implement

1.  ต้องทำตาม Scope และ Workflow ที่กำหนดในเอกสารนี้
2.  Plan Mode ต้อง Read-only จริง
3.  Execute Mode ทำเฉพาะ Approved Plan Version ล่าสุด
4.  ผู้ใช้เป็นผู้สร้าง MR และ Source Branch
5.  Reviewer ต้องผ่านก่อน Commit ทุก Task
6.  ห้ามแก้ไขนอก Scope และห้ามทำลาย User Changes
7.  Build เป็น Mandatory Verification
8.  ทุก Execute Run ต้องมี Execution Report หนึ่งรายการ
9.  Phase 1 ไม่ทำ Persistent Recovery หรือ Deployment
10. หากพบ Requirement ที่ยังไม่ชัดเจนหรือขัดแย้งกัน ต้องระบุเป็น Open Decision
    และขอข้อสรุปก่อน Implement ส่วนนั้น ห้ามเดาเป็นข้อกำหนดเอง
