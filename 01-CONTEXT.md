# Project 01: Real-Time Price Monitoring Pipeline

## Project Context (Why This Project?)

### Problem Definition

Price data in Korea is scattered across multiple agencies (Bank of Korea, Statistics Korea, Public Data Portal), each with different update cycles, schemas, and quality levels. There is also a significant gap between the prices consumers actually experience (online/mart prices) and official statistics. This pipeline integrates these heterogeneous sources to track price changes by product category.

### What This Project Proves as a DE Portfolio Piece

1. **Messy real-world data handling**: Normalizing data from sources with different schemas and inconsistent quality — not just loading clean CSVs
2. **Architectural judgment**: Being able to explain "why" for every technology choice
3. **Failure-mode design**: Detection, alerting, and recovery when sources go down or schemas change
4. **Cost awareness**: Estimating operational costs and scaling scenarios for this pipeline
5. **Transparent AI usage**: Clear distinction between AI-generated code and human-made decisions
