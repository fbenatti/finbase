--
-- PostgreSQL database cluster dump
--

\restrict FhUKz5L8dfPG7ihOP3GyGqOLtpq1FNgmmxnWu9I4Z3q3myt2oLX4NHQBDSQoxWm

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

--
-- Drop databases (except postgres and template1)
--

DROP DATABASE IF EXISTS finops;




--
-- Drop roles
--

DROP ROLE IF EXISTS finops;


--
-- Roles
--

CREATE ROLE finops;
ALTER ROLE finops WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:3Ggn7bs/z61usqMPqucIQA==$tOBvYOVeqhYHyyAumRPSCWMFbTowoSWO/HrPLCi7E8U=:VyNnKHaH/Cr+F+F3up/rD1PdTS9mBW7eouHtNqByJ6M=';

--
-- User Configurations
--








\unrestrict FhUKz5L8dfPG7ihOP3GyGqOLtpq1FNgmmxnWu9I4Z3q3myt2oLX4NHQBDSQoxWm

--
-- Databases
--

--
-- Database "template1" dump
--

--
-- PostgreSQL database dump
--

\restrict Gs0ik4CLEgcDqtIfIFLjMkdYwLgIHdBfKm90Ok7DakZ7FGU9D733ctJWI7iZAAu

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

UPDATE pg_catalog.pg_database SET datistemplate = false WHERE datname = 'template1';
DROP DATABASE template1;
--
-- Name: template1; Type: DATABASE; Schema: -; Owner: finops
--

CREATE DATABASE template1 WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE template1 OWNER TO finops;

\unrestrict Gs0ik4CLEgcDqtIfIFLjMkdYwLgIHdBfKm90Ok7DakZ7FGU9D733ctJWI7iZAAu
\connect template1
\restrict Gs0ik4CLEgcDqtIfIFLjMkdYwLgIHdBfKm90Ok7DakZ7FGU9D733ctJWI7iZAAu

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: DATABASE template1; Type: COMMENT; Schema: -; Owner: finops
--

COMMENT ON DATABASE template1 IS 'default template for new databases';


--
-- Name: template1; Type: DATABASE PROPERTIES; Schema: -; Owner: finops
--

ALTER DATABASE template1 IS_TEMPLATE = true;


\unrestrict Gs0ik4CLEgcDqtIfIFLjMkdYwLgIHdBfKm90Ok7DakZ7FGU9D733ctJWI7iZAAu
\connect template1
\restrict Gs0ik4CLEgcDqtIfIFLjMkdYwLgIHdBfKm90Ok7DakZ7FGU9D733ctJWI7iZAAu

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: DATABASE template1; Type: ACL; Schema: -; Owner: finops
--

REVOKE CONNECT,TEMPORARY ON DATABASE template1 FROM PUBLIC;
GRANT CONNECT ON DATABASE template1 TO PUBLIC;


--
-- PostgreSQL database dump complete
--

\unrestrict Gs0ik4CLEgcDqtIfIFLjMkdYwLgIHdBfKm90Ok7DakZ7FGU9D733ctJWI7iZAAu

--
-- Database "finops" dump
--

--
-- PostgreSQL database dump
--

\restrict jiXj4QLsWwNRxCW2DFQOZ3axGTglurgrkwgHzUyiBuiBvKlYNseYSZelM2L9Hn8

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: finops; Type: DATABASE; Schema: -; Owner: finops
--

CREATE DATABASE finops WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE finops OWNER TO finops;

\unrestrict jiXj4QLsWwNRxCW2DFQOZ3axGTglurgrkwgHzUyiBuiBvKlYNseYSZelM2L9Hn8
\connect finops
\restrict jiXj4QLsWwNRxCW2DFQOZ3axGTglurgrkwgHzUyiBuiBvKlYNseYSZelM2L9Hn8

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: business_rule; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.business_rule (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description character varying(1000),
    target_column character varying(200) NOT NULL,
    rule_type character varying(50) NOT NULL,
    config_json json NOT NULL,
    priority integer NOT NULL,
    valid_from character varying(6),
    valid_until character varying(6),
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.business_rule OWNER TO finops;

--
-- Name: business_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.business_rule_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.business_rule_id_seq OWNER TO finops;

--
-- Name: business_rule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.business_rule_id_seq OWNED BY public.business_rule.id;


--
-- Name: classification_rule; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.classification_rule (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    source_field character varying(200) NOT NULL,
    pattern character varying(500) NOT NULL,
    pattern_type character varying(20) NOT NULL,
    match_value character varying(200) NOT NULL,
    target_field character varying(200) NOT NULL,
    priority integer NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.classification_rule OWNER TO finops;

--
-- Name: classification_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.classification_rule_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.classification_rule_id_seq OWNER TO finops;

--
-- Name: classification_rule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.classification_rule_id_seq OWNED BY public.classification_rule.id;


--
-- Name: etl_mapping; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.etl_mapping (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    source_column character varying(200) NOT NULL,
    target_column character varying(200) NOT NULL,
    transform_type character varying(50) NOT NULL,
    transform_config json,
    priority integer NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.etl_mapping OWNER TO finops;

--
-- Name: etl_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.etl_mapping_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.etl_mapping_id_seq OWNER TO finops;

--
-- Name: etl_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.etl_mapping_id_seq OWNED BY public.etl_mapping.id;


--
-- Name: ingested_blob; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.ingested_blob (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    blob_path text NOT NULL,
    blob_prefix text NOT NULL,
    billing_period character varying(6),
    last_modified timestamp with time zone NOT NULL,
    etag character varying(200),
    content_length bigint,
    row_count integer,
    status character varying(20) NOT NULL,
    ingested_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.ingested_blob OWNER TO finops;

--
-- Name: ingested_blob_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.ingested_blob_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ingested_blob_id_seq OWNER TO finops;

--
-- Name: ingested_blob_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.ingested_blob_id_seq OWNED BY public.ingested_blob.id;


--
-- Name: normalized_cost_line; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.normalized_cost_line (
    id bigint NOT NULL,
    blob_id integer NOT NULL,
    raw_id bigint,
    billing_period character varying(6) NOT NULL,
    cost_type character varying(20) NOT NULL,
    data json NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.normalized_cost_line OWNER TO finops;

--
-- Name: normalized_cost_line_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.normalized_cost_line_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.normalized_cost_line_id_seq OWNER TO finops;

--
-- Name: normalized_cost_line_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.normalized_cost_line_id_seq OWNED BY public.normalized_cost_line.id;


--
-- Name: period_status; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.period_status (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    billing_period character varying(6) NOT NULL,
    status character varying(20) NOT NULL,
    auto_close_days integer NOT NULL,
    closed_at timestamp with time zone,
    closed_by character varying(200),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.period_status OWNER TO finops;

--
-- Name: period_status_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.period_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.period_status_id_seq OWNER TO finops;

--
-- Name: period_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.period_status_id_seq OWNED BY public.period_status.id;


--
-- Name: raw_cost_line; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.raw_cost_line (
    id bigint NOT NULL,
    blob_id integer NOT NULL,
    billing_period character varying(6) NOT NULL,
    payload json NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.raw_cost_line OWNER TO finops;

--
-- Name: raw_cost_line_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.raw_cost_line_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.raw_cost_line_id_seq OWNER TO finops;

--
-- Name: raw_cost_line_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.raw_cost_line_id_seq OWNED BY public.raw_cost_line.id;


--
-- Name: source_connection; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.source_connection (
    id integer NOT NULL,
    code character varying(50) NOT NULL,
    name character varying(200) NOT NULL,
    connector_type character varying(50) NOT NULL,
    cloud_name character varying(100),
    config_json json NOT NULL,
    blob_prefixes json NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.source_connection OWNER TO finops;

--
-- Name: source_connection_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.source_connection_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.source_connection_id_seq OWNER TO finops;

--
-- Name: source_connection_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.source_connection_id_seq OWNED BY public.source_connection.id;


--
-- Name: business_rule id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.business_rule ALTER COLUMN id SET DEFAULT nextval('public.business_rule_id_seq'::regclass);


--
-- Name: classification_rule id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule ALTER COLUMN id SET DEFAULT nextval('public.classification_rule_id_seq'::regclass);


--
-- Name: etl_mapping id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_mapping ALTER COLUMN id SET DEFAULT nextval('public.etl_mapping_id_seq'::regclass);


--
-- Name: ingested_blob id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.ingested_blob ALTER COLUMN id SET DEFAULT nextval('public.ingested_blob_id_seq'::regclass);


--
-- Name: normalized_cost_line id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.normalized_cost_line ALTER COLUMN id SET DEFAULT nextval('public.normalized_cost_line_id_seq'::regclass);


--
-- Name: period_status id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.period_status ALTER COLUMN id SET DEFAULT nextval('public.period_status_id_seq'::regclass);


--
-- Name: raw_cost_line id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.raw_cost_line ALTER COLUMN id SET DEFAULT nextval('public.raw_cost_line_id_seq'::regclass);


--
-- Name: source_connection id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_connection ALTER COLUMN id SET DEFAULT nextval('public.source_connection_id_seq'::regclass);


--
-- Data for Name: business_rule; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.business_rule (id, name, description, target_column, rule_type, config_json, priority, valid_from, valid_until, active, created_at, updated_at) FROM stdin;
1	PTAX BRL - USD to BRL	Convert USD cost to BRL using PTAX rate	CostBRL	PTAX_BRL	{"source_column": "CostInBillingCurrency", "rate": 5.0}	10	\N	\N	t	2026-08-07 13:51:11.99275+00	2026-08-07 13:51:11.99275+00
2	Taxa ISS + PIS/COFINS (18.61%)	Tax base: ISS + PIS + COFINS sobre custo de serviço	Tax_ISS	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.186065}	20	\N	\N	t	2026-08-07 13:51:11.99275+00	2026-08-07 14:04:27.300487+00
3	Taxa Marketplace (13.83%)	Tax on marketplace purchases	Tax_Marketplace	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.1383}	25	\N	\N	t	2026-08-07 14:04:27.492011+00	2026-08-07 14:04:27.492011+00
4	Taxa Extra (0.83%)	Multiplicador extra sobre custo final	VL_CUSTO_FINAL_BRL	MARKUP	{"source_column": "CostBRL", "percentage": 0.0083}	90	\N	\N	t	2026-08-07 14:04:27.68077+00	2026-08-07 14:04:27.68077+00
\.


--
-- Data for Name: classification_rule; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.classification_rule (id, name, source_field, pattern, pattern_type, match_value, target_field, priority, active, created_at, updated_at) FROM stdin;
1	Projeto → Diretoria de Tecnologia	Project	tec|tech|it-	regex	Diretoria de Tecnologia	Diretoria	10	t	2026-08-07 13:51:11.99275+00	2026-08-07 13:51:11.99275+00
2	Projeto → Diretoria de Negócios	Project	biz|neg|business	regex	Diretoria de Negócios	Diretoria	20	t	2026-08-07 13:51:11.99275+00	2026-08-07 13:51:11.99275+00
3	SKU GPT → OpenAI	MeterCategory	gpt|openai|azure.?openai	regex	OpenAI	ModeloAI	5	t	2026-08-07 13:52:14.449871+00	2026-08-07 13:52:14.449871+00
\.


--
-- Data for Name: etl_mapping; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.etl_mapping (id, name, source_column, target_column, transform_type, transform_config, priority, active, created_at, updated_at) FROM stdin;
1	CostInBillingCurrency → CostUSD	CostInBillingCurrency	CostUSD	RENAME	null	0	t	2026-08-07 13:52:14.252804+00	2026-08-07 13:52:14.252804+00
\.


--
-- Data for Name: ingested_blob; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.ingested_blob (id, connection_id, blob_path, blob_prefix, billing_period, last_modified, etag, content_length, row_count, status, ingested_at) FROM stdin;
\.


--
-- Data for Name: normalized_cost_line; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.normalized_cost_line (id, blob_id, raw_id, billing_period, cost_type, data, created_at) FROM stdin;
\.


--
-- Data for Name: period_status; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.period_status (id, connection_id, billing_period, status, auto_close_days, closed_at, closed_by, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: raw_cost_line; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.raw_cost_line (id, blob_id, billing_period, payload, created_at) FROM stdin;
\.


--
-- Data for Name: source_connection; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.source_connection (id, code, name, connector_type, cloud_name, config_json, blob_prefixes, created_at, updated_at) FROM stdin;
1	azure-4p	Azure 4P Cost Management	azure_blob	Azure-4P	{"tenant_id": "0fce1554-ffcc-4e69-aa8d-9334eb2d8fd7", "client_id": "b497374e-e461-4043-ac57-406ce004c87d", "client_secret": "lsj8Q~42HyMeNhcX0vtmLx1VWuwr5vRgIiF4XbOA", "storage_account": "proazurecostsmanagement", "container_name": "4pfinops"}	["reports/Report4P-actual-cost/", "reports/Report4P-amortized-cost/"]	2026-08-07 13:51:11.99275+00	2026-08-07 13:51:11.99275+00
5	azure-ti	Azure TI Cost Management	azure_blob	Azure-TI	{"tenant_id": "78879b00-f0e9-486c-836f-56f314e47e7f", "client_id": "20fbf218-7baa-417d-af6e-41be4cc81dfc", "client_secret": "VkI8Q~el6n4n4413U.vVIHEbTl3U2rv0wCCfccSw", "storage_account": "reportfinopsti", "container_name": "tifinops"}	["reports/ReportTI-actual-cost/", "reports/ReportTI-amortized-cost/"]	2026-08-07 14:04:04.074087+00	2026-08-07 14:04:04.074087+00
6	gcp-ti	GCP TI Billing Export	gcp_bigquery	GCP-TI	{"project_id": "vivo-ti-billing", "dataset_id": "ti_bqd_billing", "table_name": "gcp_billing_export_resource_v1_01B23F_E52290_FD9D2C", "table_name_old": "gcp_billing_export_resource_v1_015AC4_5FC0B7_B1B758", "token_path": "GCP/TI/vivo-ti-billing.json"}	[]	2026-08-07 14:04:04.482384+00	2026-08-07 14:04:04.482384+00
7	mongodb-atlas-pro	MongoDB Atlas Pro	mongodb_atlas	Atlas-MongoDB	{"base_url": "https://cloud.mongodb.com/api/atlas/v1.0", "contract_type": "pro", "contract_id": "6349bada4f67da45619e737a:auayvrdi:63a95f7d-6aef-433c-8643-9c00860d6590", "usd_to_brl": 5.52, "tax_rate": 0.0}	[]	2026-08-07 14:04:04.718822+00	2026-08-07 14:04:04.718822+00
8	mongodb-atlas-enterprise	MongoDB Atlas Enterprise	mongodb_atlas	Atlas-MongoDB	{"base_url": "https://cloud.mongodb.com/api/atlas/v1.0", "contract_type": "enterprise", "contract_id": "624c8bef33f07a2b248feab0:cbgqlrlx:8317dc99-bc1a-4d89-8558-a0fc0fba789d", "usd_to_brl": 6.2, "tax_rate": 0.0}	[]	2026-08-07 14:04:04.913243+00	2026-08-07 14:04:04.913243+00
2	gcp-4p	GCP 4P Billing Export	gcp_bigquery	GCP-4P	{"project_id": "billing-344820", "dataset_id": "billingVivo", "table_name": "gcp_billing_export_resource_v1_015B69_1CB967_9A157C", "table_name_old": "gcp_billing_export_v1_01804E_42EE23_4CC802", "token_path": "GCP/vivo-4p-billing.json"}	[]	2026-08-07 13:52:01.377554+00	2026-08-07 14:04:27.093827+00
\.


--
-- Name: business_rule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.business_rule_id_seq', 4, true);


--
-- Name: classification_rule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.classification_rule_id_seq', 3, true);


--
-- Name: etl_mapping_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.etl_mapping_id_seq', 1, true);


--
-- Name: ingested_blob_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.ingested_blob_id_seq', 1, true);


--
-- Name: normalized_cost_line_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.normalized_cost_line_id_seq', 1, false);


--
-- Name: period_status_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.period_status_id_seq', 1, true);


--
-- Name: raw_cost_line_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.raw_cost_line_id_seq', 1, false);


--
-- Name: source_connection_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.source_connection_id_seq', 8, true);


--
-- Name: business_rule business_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.business_rule
    ADD CONSTRAINT business_rule_pkey PRIMARY KEY (id);


--
-- Name: classification_rule classification_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule
    ADD CONSTRAINT classification_rule_pkey PRIMARY KEY (id);


--
-- Name: etl_mapping etl_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_mapping
    ADD CONSTRAINT etl_mapping_pkey PRIMARY KEY (id);


--
-- Name: ingested_blob ingested_blob_connection_id_blob_path_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.ingested_blob
    ADD CONSTRAINT ingested_blob_connection_id_blob_path_key UNIQUE (connection_id, blob_path);


--
-- Name: ingested_blob ingested_blob_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.ingested_blob
    ADD CONSTRAINT ingested_blob_pkey PRIMARY KEY (id);


--
-- Name: normalized_cost_line normalized_cost_line_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.normalized_cost_line
    ADD CONSTRAINT normalized_cost_line_pkey PRIMARY KEY (id);


--
-- Name: period_status period_status_connection_id_billing_period_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.period_status
    ADD CONSTRAINT period_status_connection_id_billing_period_key UNIQUE (connection_id, billing_period);


--
-- Name: period_status period_status_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.period_status
    ADD CONSTRAINT period_status_pkey PRIMARY KEY (id);


--
-- Name: raw_cost_line raw_cost_line_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.raw_cost_line
    ADD CONSTRAINT raw_cost_line_pkey PRIMARY KEY (id);


--
-- Name: source_connection source_connection_code_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_connection
    ADD CONSTRAINT source_connection_code_key UNIQUE (code);


--
-- Name: source_connection source_connection_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_connection
    ADD CONSTRAINT source_connection_pkey PRIMARY KEY (id);


--
-- Name: ingested_blob ingested_blob_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.ingested_blob
    ADD CONSTRAINT ingested_blob_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: normalized_cost_line normalized_cost_line_blob_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.normalized_cost_line
    ADD CONSTRAINT normalized_cost_line_blob_id_fkey FOREIGN KEY (blob_id) REFERENCES public.ingested_blob(id);


--
-- Name: normalized_cost_line normalized_cost_line_raw_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.normalized_cost_line
    ADD CONSTRAINT normalized_cost_line_raw_id_fkey FOREIGN KEY (raw_id) REFERENCES public.raw_cost_line(id);


--
-- Name: period_status period_status_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.period_status
    ADD CONSTRAINT period_status_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: raw_cost_line raw_cost_line_blob_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.raw_cost_line
    ADD CONSTRAINT raw_cost_line_blob_id_fkey FOREIGN KEY (blob_id) REFERENCES public.ingested_blob(id);


--
-- PostgreSQL database dump complete
--

\unrestrict jiXj4QLsWwNRxCW2DFQOZ3axGTglurgrkwgHzUyiBuiBvKlYNseYSZelM2L9Hn8

--
-- Database "postgres" dump
--

--
-- PostgreSQL database dump
--

\restrict LfJheVufyrAMRE35eFIy2LrSB8nhogrZVf250RJAY6JzZW3PR2s8EVEqMwUTBN0

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

DROP DATABASE postgres;
--
-- Name: postgres; Type: DATABASE; Schema: -; Owner: finops
--

CREATE DATABASE postgres WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'en_US.utf8';


ALTER DATABASE postgres OWNER TO finops;

\unrestrict LfJheVufyrAMRE35eFIy2LrSB8nhogrZVf250RJAY6JzZW3PR2s8EVEqMwUTBN0
\connect postgres
\restrict LfJheVufyrAMRE35eFIy2LrSB8nhogrZVf250RJAY6JzZW3PR2s8EVEqMwUTBN0

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: DATABASE postgres; Type: COMMENT; Schema: -; Owner: finops
--

COMMENT ON DATABASE postgres IS 'default administrative connection database';


--
-- PostgreSQL database dump complete
--

\unrestrict LfJheVufyrAMRE35eFIy2LrSB8nhogrZVf250RJAY6JzZW3PR2s8EVEqMwUTBN0

--
-- PostgreSQL database cluster dump complete
--

