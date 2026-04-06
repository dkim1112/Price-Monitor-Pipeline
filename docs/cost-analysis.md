# Cost Analysis: AWS Deployment Estimate

This document estimates the monthly cost of running the Price Monitor Pipeline on AWS, assuming the same architecture deployed to the cloud.

## Current Local Setup

Running on a MacBook with Docker. Total cost: $0/month (excluding electricity and API rate limits, which are free-tier).

## AWS Deployment Architecture

The pipeline has three components: the PostgreSQL database, the Python collection/aggregation jobs (cron), and the Streamlit dashboard.

### Compute — EC2 (Pipeline + Dashboard)

The pipeline runs short batch jobs (KOSTAT ~10 min/week, ECOS ~1 min/month, aggregation ~30 sec) and hosts a Streamlit dashboard.

| Option | vCPU | RAM | Monthly Cost |
|--------|------|-----|-------------|
| t3.micro | 2 | 1 GB | ~$8.50 |
| t3.small | 2 | 2 GB | ~$17.00 |

**Recommendation: t3.small ($17/month)** — the KOSTAT collector processes 600K+ XML records and benefits from 2 GB RAM. The t3.micro would work but might swap during collection. Streamlit needs minimal resources for a single-user dashboard.

Alternative: Lambda + EventBridge for the collection jobs (~$0.10/month) and a separate t3.micro for the dashboard ($8.50). Total ~$9, but adds deployment complexity.

### Database — RDS PostgreSQL

Current data volume: ~500K rows/week for KOSTAT, ~600 rows/month for ECOS. After one year of operation, expect ~25M raw rows (~3 GB) plus ~6K mart rows (<1 MB).

| Option | Instance | Storage | Monthly Cost |
|--------|----------|---------|-------------|
| RDS db.t3.micro | 2 vCPU, 1 GB | 20 GB gp3 | ~$15.00 |
| RDS db.t3.small | 2 vCPU, 2 GB | 20 GB gp3 | ~$29.00 |

**Recommendation: db.t3.micro ($15/month)** — the workload is light (weekly batch inserts, occasional dashboard queries). 20 GB gp3 storage is sufficient for over a year of data. No read replicas needed.

Alternative: Aurora Serverless v2 starts at ~$0.12/ACU-hour. With bursty usage (active only during collection + dashboard views), could be cheaper (~$5-8/month) but pricing is harder to predict.

### Storage — S3 (Optional, for log archival)

| Purpose | Volume | Monthly Cost |
|---------|--------|-------------|
| Pipeline logs | ~10 MB/month | ~$0.01 |
| DB backups (pg_dump) | ~500 MB/month | ~$0.01 |

**Negligible cost.** S3 is optional but good practice for log retention beyond the EC2 instance.

### Networking

| Item | Monthly Cost |
|------|-------------|
| Elastic IP | $3.65 (if not attached to running instance) |
| Data transfer out | ~$0.00 (minimal, dashboard is single-user) |
| NAT Gateway | $0 (not needed for this architecture) |

### API Costs

Both APIs are free:

| API | Rate Limit | Our Usage | Cost |
|-----|-----------|-----------|------|
| KOSTAT (data.go.kr) | 10,000 req/day | ~150 req/week | Free |
| ECOS (Bank of Korea) | 100,000 req/day | ~10 req/month | Free |

## Monthly Total

| Component | Cost |
|-----------|------|
| EC2 t3.small (pipeline + dashboard) | $17.00 |
| RDS db.t3.micro (PostgreSQL) | $15.00 |
| EBS/gp3 (20 GB) | included in RDS |
| S3 (logs + backups) | $0.02 |
| Data transfer | $0.00 |
| APIs | $0.00 |
| **Total** | **~$32/month** |

With some buffer for CloudWatch logging, occasional snapshots, and overhead: **$35–50/month** is a realistic estimate.

## Cost Optimization Options

**Cheapest viable option (~$10/month)**: Use a single t3.micro ($8.50) with SQLite or local PostgreSQL instead of RDS. Trades reliability for cost. Acceptable for a personal project, not for production.

**Serverless option (~$5–15/month)**: Lambda for collection jobs + Aurora Serverless v2 for the database. No dashboard hosting (use Streamlit Cloud free tier instead). Most cost-efficient but adds deployment complexity.

**Free tier (Year 1)**: AWS Free Tier includes 750 hours of t3.micro and 750 hours of db.t3.micro per month for 12 months. If both qualify, the pipeline runs free for the first year, then jumps to ~$32/month.

## GCP Comparison

| AWS | GCP Equivalent | Estimated Cost |
|-----|---------------|----------------|
| EC2 t3.small | e2-small | ~$15.00 |
| RDS db.t3.micro | Cloud SQL db-f1-micro | ~$10.00 |
| S3 | Cloud Storage | ~$0.02 |
| **Total** | | **~$25/month** |

GCP is slightly cheaper for this workload due to Cloud SQL's lower entry price. Both platforms offer comparable free tiers for the first year.

## Scaling Considerations

The current architecture handles the workload comfortably. Scaling triggers would be:

- **More data sources** (10+): Consider Airflow on ECS Fargate for orchestration (~$15/month additional).
- **Larger data volume** (100M+ rows): Upgrade to db.t3.small or db.t3.medium, add table partitioning by year (already partitioned by month).
- **Multiple dashboard users** (10+): Add an ALB ($16/month) or move to Streamlit Cloud.
- **Real-time ingestion**: Replace cron with Kafka/Kinesis — fundamentally different architecture, much higher cost ($100+/month).
