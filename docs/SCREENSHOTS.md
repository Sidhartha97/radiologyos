# RadiologyOS portfolio screenshots

These selected images show the deployed application, historical-report ingestion, usage monitoring, and delivery workflow. Screenshots were cropped or redacted to omit report text and direct user/workspace identifiers. Keep future captures limited to synthetic data; do not publish real patient records, credentials, account identifiers, or secret values.

## Cloud architecture

![AWS infrastructure diagram showing the dedicated VPC, public Application Load Balancer, private EC2 Auto Scaling Group, PostgreSQL, and managed AWS services](images/aws-cloud-infrastructure.png)

**AWS cloud infrastructure.** Dedicated VPC, public ALB, private EC2 Auto Scaling Group, private PostgreSQL, and connected managed AWS services. This depicts the development deployment; the HTTP listener and single NAT Gateway are documented limitations.

![Report generation workflow from authenticated user request through tenant-filtered historical-report retrieval, Bedrock drafting, and clinician review](images/report-generation-workflow.png)

**Report-generation workflow.** Shows the request path, tenant and modality filtered retrieval from uploaded historical reports, AI drafting, and clinician review before finalization.

## Application and operations

![RadiologyOS sign-in page with email and hospital employee sign-in options](images/sign-in-page.png)

**Sign-in page.** Shows the authentication entry point for email and hospital employee credentials. The capture contains no entered credentials.

![Platform administrator overview with account, membership, activity, and operations panels](images/admin-overview.png)

**Platform admin panel.** Shows the administrative overview and links into customer management, AI usage visibility, and manual payments.

![Radiologist workspace dashboard with patient and workspace identifiers removed](images/radiologist-workspace.png)

**Radiologist workspace.** Shows the clinical workspace dashboard and report activity panels. User/workspace labels were removed from the screenshot.

![Workspace dashboard with patient-identifying details excluded](images/workspace-dashboard.png)

**Workspace dashboard.** Application overview metrics; patient-level rows and identifying details are excluded.

![Report editor with the assistant chat open and a sample prompt shown with clinical details redacted](images/ai-chat-input-redacted.png)

**AI chat input.** Shows the report editor beside the prompt composer. Patient details and the actual clinical prompt are redacted.

![Report editor with the assistant's generated draft panel open and clinical report text redacted](images/ai-chat-output-redacted.png)

**AI chat output.** Shows the generated draft preview and the option to insert it into the report editor. Patient and report text are redacted; generated content remains a clinician-reviewed draft.

![Historical report library with 100 selected USG reports marked ready for upload](images/historical-library-validation.png)

**Historical-library validation.** Shows a batch of 100 reference reports validated and ready for upload.

![Historical report files uploaded to S3 while indexing is queued](images/historical-library-upload-progress.png)

**Upload and indexing queue.** Shows the browser upload state and indexing handoff for selected historical reports.

![Per-file historical-report indexing status showing indexed and indexing examples](images/historical-library-indexing-status.png)

**Per-document indexing status.** Illustrates that indexing progress is tracked for individual uploaded files.

![Token usage dashboard with user and workspace identifiers redacted](images/token-usage-dashboard-redacted.png)

**Token usage monitoring.** Displays aggregate input, output, and total model token counters with user and workspace identifiers redacted. These are usage counters, not a billing ledger.

## CI/CD

![GitHub Actions workflow run with verification and ARM image build and deployment jobs succeeded](images/ci-cd-deployment-success.png)

**GitHub Actions deployment.** Shows successful verification and image-build/deployment jobs for a workflow run.
