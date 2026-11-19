# AAD — Banking Data Knowledge Base

## Dataset

Name: Bank Sample Database
Version: 2
Size: 100K transactions
Database: PostgreSQL
Schema: public
Tables: 33

## Official documentation

https://databasesample.com/datasets/bank-sample-database

The official documentation includes:
- Entity relationship diagram (Mermaid)
- Table definitions
- Primary and foreign keys
- Banking data consistency rules
- SQL examples

License: CC0 1.0

## Local documentation

- data-dictionary.csv: Actual database columns and types
- relationships.csv: Actual foreign key relationships
- schema.sql: Database DDL extracted from PostgreSQL

## AIF usage

The Data Architect agent must:

1. Discover database entities and relationships.
2. Consult official domain documentation.
3. Verify assumptions against the actual schema.
4. Identify source tables and fields for requirements.
5. Report uncertain business interpretations.
6. Use read-only database access.

The agent must not invent business rules.
