--
-- PostgreSQL database cluster dump
--

\restrict PZM56UBEbiQuWIhbhpuoFr8VYh5JaDFCcz1xbrvYPPRsQ5kUygGE255q1Jfkl1f

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
ALTER ROLE finops WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:WGMUQ0uZ4dlR45yfdRb6Sg==$Z+3sKkwXGrj3xV5mGlp/2/Ao8dRKYjsmv0OmmLCDihs=:Sz8FaiVqht8p7BPMDgte0Ecg+Jlu5fdQoMRKIhKBlC0=';

--
-- User Configurations
--








\unrestrict PZM56UBEbiQuWIhbhpuoFr8VYh5JaDFCcz1xbrvYPPRsQ5kUygGE255q1Jfkl1f

--
-- Databases
--

--
-- Database "template1" dump
--

--
-- PostgreSQL database dump
--

\restrict IZ6XYpteXLEZp1eH6CWmBbCVSHsScdmXhewpAqA9wUIHrjzy4d0p241ncDmNmBb

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

\unrestrict IZ6XYpteXLEZp1eH6CWmBbCVSHsScdmXhewpAqA9wUIHrjzy4d0p241ncDmNmBb
\connect template1
\restrict IZ6XYpteXLEZp1eH6CWmBbCVSHsScdmXhewpAqA9wUIHrjzy4d0p241ncDmNmBb

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


\unrestrict IZ6XYpteXLEZp1eH6CWmBbCVSHsScdmXhewpAqA9wUIHrjzy4d0p241ncDmNmBb
\connect template1
\restrict IZ6XYpteXLEZp1eH6CWmBbCVSHsScdmXhewpAqA9wUIHrjzy4d0p241ncDmNmBb

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

\unrestrict IZ6XYpteXLEZp1eH6CWmBbCVSHsScdmXhewpAqA9wUIHrjzy4d0p241ncDmNmBb

--
-- Database "finops" dump
--

--
-- PostgreSQL database dump
--

\restrict wJw5l13uCEhBIcO9Z8NzZFpZpiJQZT0bZYuTn2cgNIAIlLNZ415AS3PKixaVe7e

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

\unrestrict wJw5l13uCEhBIcO9Z8NzZFpZpiJQZT0bZYuTn2cgNIAIlLNZ415AS3PKixaVe7e
\connect finops
\restrict wJw5l13uCEhBIcO9Z8NzZFpZpiJQZT0bZYuTn2cgNIAIlLNZ415AS3PKixaVe7e

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
    connection_id integer,
    destination_table_id integer,
    name character varying(200) NOT NULL,
    description character varying(1000),
    target_column character varying(200) NOT NULL,
    rule_type character varying(50) NOT NULL,
    config_json json NOT NULL,
    conditions json,
    expression character varying(1000),
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
    connection_id integer,
    destination_table_id integer,
    department_id integer,
    project_id integer,
    name character varying(200) NOT NULL,
    source_field character varying(200) NOT NULL,
    pattern character varying(500) NOT NULL,
    pattern_type character varying(20) NOT NULL,
    match_value character varying(200) NOT NULL,
    target_field character varying(200) NOT NULL,
    conditions json,
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
-- Name: clone_price_pendency; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.clone_price_pendency (
    id integer NOT NULL,
    clone_rule_id integer,
    destination_table_id integer NOT NULL,
    connection_id integer,
    provider character varying(100),
    meter_id character varying(300),
    billing_period character varying(6),
    source_record_id character varying(100),
    reason character varying(500) NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.clone_price_pendency OWNER TO finops;

--
-- Name: clone_price_pendency_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.clone_price_pendency_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.clone_price_pendency_id_seq OWNER TO finops;

--
-- Name: clone_price_pendency_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.clone_price_pendency_id_seq OWNED BY public.clone_price_pendency.id;


--
-- Name: clone_rule; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.clone_rule (
    id integer NOT NULL,
    destination_table_id integer NOT NULL,
    connection_id integer,
    name character varying(200) NOT NULL,
    description character varying(1000),
    provider character varying(100),
    conditions json NOT NULL,
    roles json NOT NULL,
    original_changes json NOT NULL,
    clone_changes json NOT NULL,
    valid_from character varying(6),
    valid_until character varying(6),
    priority integer NOT NULL,
    active boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.clone_rule OWNER TO finops;

--
-- Name: clone_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.clone_rule_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.clone_rule_id_seq OWNER TO finops;

--
-- Name: clone_rule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.clone_rule_id_seq OWNED BY public.clone_rule.id;


--
-- Name: dashboard; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.dashboard (
    id integer NOT NULL,
    name character varying(160) NOT NULL,
    pivot_view_id integer,
    global_filters json NOT NULL,
    layout_json json NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.dashboard OWNER TO finops;

--
-- Name: dashboard_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.dashboard_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dashboard_id_seq OWNER TO finops;

--
-- Name: dashboard_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.dashboard_id_seq OWNED BY public.dashboard.id;


--
-- Name: dashboard_widget; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.dashboard_widget (
    id integer NOT NULL,
    dashboard_id integer NOT NULL,
    pivot_view_id integer,
    title character varying(200) NOT NULL,
    chart_type character varying(30) NOT NULL,
    config_json json NOT NULL,
    position_json json NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.dashboard_widget OWNER TO finops;

--
-- Name: dashboard_widget_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.dashboard_widget_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dashboard_widget_id_seq OWNER TO finops;

--
-- Name: dashboard_widget_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.dashboard_widget_id_seq OWNED BY public.dashboard_widget.id;


--
-- Name: department; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.department (
    id integer NOT NULL,
    parent_id integer,
    name character varying(200) NOT NULL,
    centro_custo character varying(50),
    ambiente character varying(50),
    cloud_filter character varying(50),
    custom_fields json,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.department OWNER TO finops;

--
-- Name: department_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.department_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.department_id_seq OWNER TO finops;

--
-- Name: department_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.department_id_seq OWNED BY public.department.id;


--
-- Name: destination_column; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.destination_column (
    id integer NOT NULL,
    destination_table_id integer NOT NULL,
    column_name character varying(200) NOT NULL,
    data_type character varying(50) NOT NULL,
    is_required boolean NOT NULL,
    "position" integer NOT NULL,
    default_value character varying(500),
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.destination_column OWNER TO finops;

--
-- Name: destination_column_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.destination_column_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.destination_column_id_seq OWNER TO finops;

--
-- Name: destination_column_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.destination_column_id_seq OWNED BY public.destination_column.id;


--
-- Name: destination_table; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.destination_table (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description character varying(500),
    physical_name character varying(100) NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.destination_table OWNER TO finops;

--
-- Name: destination_table_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.destination_table_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.destination_table_id_seq OWNER TO finops;

--
-- Name: destination_table_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.destination_table_id_seq OWNED BY public.destination_table.id;


--
-- Name: etl_mapping; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.etl_mapping (
    id integer NOT NULL,
    connection_id integer,
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
-- Name: etl_task; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.etl_task (
    id integer NOT NULL,
    connection_id integer,
    destination_table_id integer,
    task_type character varying(20) NOT NULL,
    status character varying(40) NOT NULL,
    total_blobs integer NOT NULL,
    processed_blobs integer NOT NULL,
    rows_raw integer NOT NULL,
    rows_normalized integer NOT NULL,
    rows_destination integer NOT NULL,
    current_blob character varying(500),
    total_download_bytes bigint NOT NULL,
    downloaded_bytes_total bigint NOT NULL,
    current_blob_size bigint NOT NULL,
    download_bytes bigint NOT NULL,
    active_downloads integer NOT NULL,
    download_blobs json NOT NULL,
    phase character varying(200),
    connection_ids json,
    billing_periods json,
    selected_blobs json,
    errors json NOT NULL,
    result_json json,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone
);


ALTER TABLE public.etl_task OWNER TO finops;

--
-- Name: etl_task_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.etl_task_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.etl_task_id_seq OWNER TO finops;

--
-- Name: etl_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.etl_task_id_seq OWNED BY public.etl_task.id;


--
-- Name: forecast_adjustment; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.forecast_adjustment (
    id integer NOT NULL,
    forecast_config_id integer NOT NULL,
    name character varying(200) NOT NULL,
    filters json NOT NULL,
    adjustment_type character varying(30) NOT NULL,
    percentage double precision,
    fixed_value double precision,
    start_period character varying(6),
    end_period character varying(6),
    priority integer NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.forecast_adjustment OWNER TO finops;

--
-- Name: forecast_adjustment_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.forecast_adjustment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_adjustment_id_seq OWNER TO finops;

--
-- Name: forecast_adjustment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.forecast_adjustment_id_seq OWNED BY public.forecast_adjustment.id;


--
-- Name: forecast_business_rule; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.forecast_business_rule (
    id integer NOT NULL,
    forecast_config_id integer NOT NULL,
    name character varying(200) NOT NULL,
    identification_column character varying(200) NOT NULL,
    identification_values json NOT NULL,
    charge_type character varying(40) NOT NULL,
    behavior character varying(40) NOT NULL,
    reference_column character varying(200),
    reference_values json NOT NULL,
    value_column character varying(200),
    start_period character varying(6) NOT NULL,
    end_period character varying(6),
    growth_rate double precision NOT NULL,
    replacement_value double precision,
    cancel_on_refund boolean NOT NULL,
    scope json NOT NULL,
    priority integer NOT NULL,
    active boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.forecast_business_rule OWNER TO finops;

--
-- Name: forecast_business_rule_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.forecast_business_rule_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_business_rule_id_seq OWNER TO finops;

--
-- Name: forecast_business_rule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.forecast_business_rule_id_seq OWNED BY public.forecast_business_rule.id;


--
-- Name: forecast_config; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.forecast_config (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    description character varying(1000),
    destination_table_id integer NOT NULL,
    measure_column character varying(200) NOT NULL,
    currency_column character varying(200),
    date_column character varying(200) NOT NULL,
    period_column character varying(200),
    type_column character varying(200),
    dimensions json NOT NULL,
    cutoff_days integer NOT NULL,
    horizon_months integer NOT NULL,
    growth_rate double precision NOT NULL,
    model_mode character varying(30) NOT NULL,
    enabled_windows json NOT NULL,
    recent_weight double precision NOT NULL,
    minimum_daily_points integer NOT NULL,
    minimum_monthly_points integer NOT NULL,
    null_dimension_label character varying(100) NOT NULL,
    active boolean NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.forecast_config OWNER TO finops;

--
-- Name: forecast_config_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.forecast_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_config_id_seq OWNER TO finops;

--
-- Name: forecast_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.forecast_config_id_seq OWNED BY public.forecast_config.id;


--
-- Name: forecast_run; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.forecast_run (
    id integer NOT NULL,
    forecast_config_id integer,
    config_version integer NOT NULL,
    trigger_type character varying(30) NOT NULL,
    source_etl_task_id integer,
    cutoff_date character varying(10) NOT NULL,
    status character varying(30) NOT NULL,
    phase character varying(200),
    total_series integer NOT NULL,
    processed_series integer NOT NULL,
    rows_written integer NOT NULL,
    selected_models json NOT NULL,
    error_metrics json NOT NULL,
    result_table character varying(200),
    config_snapshot json NOT NULL,
    destination_table_id_snapshot integer,
    destination_physical_name character varying(200),
    source_snapshot_id character varying(100),
    error character varying(2000),
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone
);


ALTER TABLE public.forecast_run OWNER TO finops;

--
-- Name: forecast_run_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.forecast_run_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.forecast_run_id_seq OWNER TO finops;

--
-- Name: forecast_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.forecast_run_id_seq OWNED BY public.forecast_run.id;


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
-- Name: pivot_view; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.pivot_view (
    id integer NOT NULL,
    name character varying(160) NOT NULL,
    destination_table_id integer NOT NULL,
    config_json json NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.pivot_view OWNER TO finops;

--
-- Name: pivot_view_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.pivot_view_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pivot_view_id_seq OWNER TO finops;

--
-- Name: pivot_view_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.pivot_view_id_seq OWNED BY public.pivot_view.id;


--
-- Name: project; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.project (
    id integer NOT NULL,
    department_id integer NOT NULL,
    projeto_name character varying(200) NOT NULL,
    cloud character varying(50),
    subscription_rg character varying(500),
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.project OWNER TO finops;

--
-- Name: project_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.project_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.project_id_seq OWNER TO finops;

--
-- Name: project_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.project_id_seq OWNED BY public.project.id;


--
-- Name: scheduled_task; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.scheduled_task (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    connection_id integer NOT NULL,
    destination_reprocess boolean NOT NULL,
    cron_expression character varying(100) NOT NULL,
    timezone character varying(50) NOT NULL,
    active boolean NOT NULL,
    last_run_at timestamp with time zone,
    next_run_at timestamp with time zone,
    config_json json NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.scheduled_task OWNER TO finops;

--
-- Name: scheduled_task_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.scheduled_task_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.scheduled_task_id_seq OWNER TO finops;

--
-- Name: scheduled_task_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.scheduled_task_id_seq OWNED BY public.scheduled_task.id;


--
-- Name: skipped_blob; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.skipped_blob (
    id integer NOT NULL,
    connection_id integer NOT NULL,
    blob_path text NOT NULL,
    reason character varying(200),
    skipped_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.skipped_blob OWNER TO finops;

--
-- Name: skipped_blob_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.skipped_blob_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.skipped_blob_id_seq OWNER TO finops;

--
-- Name: skipped_blob_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.skipped_blob_id_seq OWNED BY public.skipped_blob.id;


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
-- Name: source_destination_mapping; Type: TABLE; Schema: public; Owner: finops
--

CREATE TABLE public.source_destination_mapping (
    id integer NOT NULL,
    source_connection_id integer NOT NULL,
    destination_table_id integer NOT NULL,
    name character varying(200),
    source_column character varying(200) NOT NULL,
    destination_column character varying(200) NOT NULL,
    transform_type character varying(50) NOT NULL,
    transform_config json,
    priority integer NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.source_destination_mapping OWNER TO finops;

--
-- Name: source_destination_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: finops
--

CREATE SEQUENCE public.source_destination_mapping_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.source_destination_mapping_id_seq OWNER TO finops;

--
-- Name: source_destination_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: finops
--

ALTER SEQUENCE public.source_destination_mapping_id_seq OWNED BY public.source_destination_mapping.id;


--
-- Name: business_rule id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.business_rule ALTER COLUMN id SET DEFAULT nextval('public.business_rule_id_seq'::regclass);


--
-- Name: classification_rule id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule ALTER COLUMN id SET DEFAULT nextval('public.classification_rule_id_seq'::regclass);


--
-- Name: clone_price_pendency id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_price_pendency ALTER COLUMN id SET DEFAULT nextval('public.clone_price_pendency_id_seq'::regclass);


--
-- Name: clone_rule id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_rule ALTER COLUMN id SET DEFAULT nextval('public.clone_rule_id_seq'::regclass);


--
-- Name: dashboard id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard ALTER COLUMN id SET DEFAULT nextval('public.dashboard_id_seq'::regclass);


--
-- Name: dashboard_widget id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard_widget ALTER COLUMN id SET DEFAULT nextval('public.dashboard_widget_id_seq'::regclass);


--
-- Name: department id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.department ALTER COLUMN id SET DEFAULT nextval('public.department_id_seq'::regclass);


--
-- Name: destination_column id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_column ALTER COLUMN id SET DEFAULT nextval('public.destination_column_id_seq'::regclass);


--
-- Name: destination_table id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_table ALTER COLUMN id SET DEFAULT nextval('public.destination_table_id_seq'::regclass);


--
-- Name: etl_mapping id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_mapping ALTER COLUMN id SET DEFAULT nextval('public.etl_mapping_id_seq'::regclass);


--
-- Name: etl_task id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_task ALTER COLUMN id SET DEFAULT nextval('public.etl_task_id_seq'::regclass);


--
-- Name: forecast_adjustment id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_adjustment ALTER COLUMN id SET DEFAULT nextval('public.forecast_adjustment_id_seq'::regclass);


--
-- Name: forecast_business_rule id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_business_rule ALTER COLUMN id SET DEFAULT nextval('public.forecast_business_rule_id_seq'::regclass);


--
-- Name: forecast_config id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_config ALTER COLUMN id SET DEFAULT nextval('public.forecast_config_id_seq'::regclass);


--
-- Name: forecast_run id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_run ALTER COLUMN id SET DEFAULT nextval('public.forecast_run_id_seq'::regclass);


--
-- Name: ingested_blob id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.ingested_blob ALTER COLUMN id SET DEFAULT nextval('public.ingested_blob_id_seq'::regclass);


--
-- Name: period_status id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.period_status ALTER COLUMN id SET DEFAULT nextval('public.period_status_id_seq'::regclass);


--
-- Name: pivot_view id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.pivot_view ALTER COLUMN id SET DEFAULT nextval('public.pivot_view_id_seq'::regclass);


--
-- Name: project id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.project ALTER COLUMN id SET DEFAULT nextval('public.project_id_seq'::regclass);


--
-- Name: scheduled_task id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.scheduled_task ALTER COLUMN id SET DEFAULT nextval('public.scheduled_task_id_seq'::regclass);


--
-- Name: skipped_blob id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.skipped_blob ALTER COLUMN id SET DEFAULT nextval('public.skipped_blob_id_seq'::regclass);


--
-- Name: source_connection id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_connection ALTER COLUMN id SET DEFAULT nextval('public.source_connection_id_seq'::regclass);


--
-- Name: source_destination_mapping id; Type: DEFAULT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_destination_mapping ALTER COLUMN id SET DEFAULT nextval('public.source_destination_mapping_id_seq'::regclass);


--
-- Data for Name: business_rule; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.business_rule (id, connection_id, destination_table_id, name, description, target_column, rule_type, config_json, conditions, expression, priority, valid_from, valid_until, active, created_at, updated_at) FROM stdin;
1	1	\N	PTAX BRL - USD to BRL (Azure default)	Convert USD cost to BRL, default PTAX rate	CostBRL	PTAX_BRL	{"source_column": "CostInBillingCurrency", "rate": 5.0}	\N	\N	10	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
2	1	\N	Taxa ISS + PIS/COFINS (18.61%)	ISS + PIS + COFINS on service cost	Tax_Base	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.186065}	\N	\N	20	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
3	1	\N	Taxa Marketplace (13.83%)	Tax on marketplace purchases (Azure Marketplace)	Tax_Marketplace	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.1383}	\N	\N	25	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
4	1	\N	Taxa Extra (0.83%)	Multiplier on final BRL cost	VL_CUSTO_FINAL_BRL	MARKUP	{"source_column": "CostBRL", "percentage": 0.0083}	\N	\N	90	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
5	2	\N	PTAX BRL - USD to BRL (Azure default)	Convert USD cost to BRL, default PTAX rate	CostBRL	PTAX_BRL	{"source_column": "CostInBillingCurrency", "rate": 5.0}	\N	\N	10	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
6	2	\N	Taxa ISS + PIS/COFINS (18.61%)	ISS + PIS + COFINS on service cost	Tax_Base	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.186065}	\N	\N	20	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
7	2	\N	Taxa Marketplace (13.83%)	Tax on marketplace purchases (Azure Marketplace)	Tax_Marketplace	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.1383}	\N	\N	25	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
8	2	\N	Taxa Extra (0.83%)	Multiplier on final BRL cost	VL_CUSTO_FINAL_BRL	MARKUP	{"source_column": "CostBRL", "percentage": 0.0083}	\N	\N	90	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
9	5	\N	PTAX BRL - USD to BRL (MongoDB Pro: 5.52)	Convert USD to BRL at contract rate 5.52	CostBRL	PTAX_BRL	{"source_column": "CostInBillingCurrency", "rate": 5.52}	\N	\N	10	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
10	5	\N	Tax ISS+PIS+COFINS MongoDB Pro (0%)	MongoDB Pro contract tax rate	Tax_Base	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.0}	\N	\N	20	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
11	6	\N	PTAX BRL - USD to BRL (MongoDB Enterprise: 6.2)	Convert USD to BRL at contract rate 6.2	CostBRL	PTAX_BRL	{"source_column": "CostInBillingCurrency", "rate": 6.2}	\N	\N	10	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
12	6	\N	Tax ISS+PIS+COFINS MongoDB Enterprise (0%)	MongoDB Enterprise contract tax rate	Tax_Base	TAX_BASE	{"source_column": "CostInBillingCurrency", "rate": 0.0}	\N	\N	20	\N	\N	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
\.


--
-- Data for Name: classification_rule; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.classification_rule (id, connection_id, destination_table_id, department_id, project_id, name, source_field, pattern, pattern_type, match_value, target_field, conditions, priority, active, created_at, updated_at) FROM stdin;
1	1	\N	\N	\N	Projeto -> Diretoria de Tecnologia	Project	tec|tech|it-	regex	Diretoria de Tecnologia	Diretoria	\N	10	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
2	1	\N	\N	\N	Projeto -> Diretoria de Negocios	Project	biz|neg|business	regex	Diretoria de Negocios	Diretoria	\N	20	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
3	1	\N	\N	\N	SKU GPT/OpenAI -> Modelo AI	MeterCategory	gpt|openai|azure.?openai	regex	OpenAI	ModeloAI	\N	5	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
4	2	\N	\N	\N	Projeto -> Diretoria de Tecnologia	Project	tec|tech|it-	regex	Diretoria de Tecnologia	Diretoria	\N	10	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
5	2	\N	\N	\N	Projeto -> Diretoria de Negocios	Project	biz|neg|business	regex	Diretoria de Negocios	Diretoria	\N	20	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
6	2	\N	\N	\N	SKU GPT/OpenAI -> Modelo AI	MeterCategory	gpt|openai|azure.?openai	regex	OpenAI	ModeloAI	\N	5	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
\.


--
-- Data for Name: clone_price_pendency; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.clone_price_pendency (id, clone_rule_id, destination_table_id, connection_id, provider, meter_id, billing_period, source_record_id, reason, created_at) FROM stdin;
\.


--
-- Data for Name: clone_rule; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.clone_rule (id, destination_table_id, connection_id, name, description, provider, conditions, roles, original_changes, clone_changes, valid_from, valid_until, priority, active, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: dashboard; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.dashboard (id, name, pivot_view_id, global_filters, layout_json, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: dashboard_widget; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.dashboard_widget (id, dashboard_id, pivot_view_id, title, chart_type, config_json, position_json, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: department; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.department (id, parent_id, name, centro_custo, ambiente, cloud_filter, custom_fields, active, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: destination_column; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.destination_column (id, destination_table_id, column_name, data_type, is_required, "position", default_value, created_at) FROM stdin;
\.


--
-- Data for Name: destination_table; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.destination_table (id, name, description, physical_name, active, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: etl_mapping; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.etl_mapping (id, connection_id, name, source_column, target_column, transform_type, transform_config, priority, active, created_at, updated_at) FROM stdin;
1	1	Date -> date	Date	date	RENAME	\N	1	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
2	1	SubscriptionName -> subscription_name	SubscriptionName	subscription_name	RENAME	\N	2	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
3	1	ResourceGroup -> resource_group	ResourceGroup	resource_group	RENAME	\N	3	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
4	1	ServiceName -> service_name	ServiceName	service_name	RENAME	\N	4	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
5	1	MeterCategory -> meter_category	MeterCategory	meter_category	RENAME	\N	5	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
6	1	ResourceLocation -> region	ResourceLocation	region	RENAME	\N	6	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
7	1	ConsumedQuantity -> quantity	ConsumedQuantity	quantity	RENAME	\N	7	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
8	1	EffectivePrice -> effective_price	EffectivePrice	effective_price	RENAME	\N	8	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
9	1	CostInBillingCurrency -> cost_usd	CostInBillingCurrency	cost_usd	RENAME	\N	9	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
10	2	Date -> date	Date	date	RENAME	\N	1	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
11	2	SubscriptionName -> subscription_name	SubscriptionName	subscription_name	RENAME	\N	2	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
12	2	ResourceGroup -> resource_group	ResourceGroup	resource_group	RENAME	\N	3	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
13	2	ServiceName -> service_name	ServiceName	service_name	RENAME	\N	4	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
14	2	MeterCategory -> meter_category	MeterCategory	meter_category	RENAME	\N	5	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
15	2	ResourceLocation -> region	ResourceLocation	region	RENAME	\N	6	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
16	2	ConsumedQuantity -> quantity	ConsumedQuantity	quantity	RENAME	\N	7	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
17	2	EffectivePrice -> effective_price	EffectivePrice	effective_price	RENAME	\N	8	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
18	2	CostInBillingCurrency -> cost_usd	CostInBillingCurrency	cost_usd	RENAME	\N	9	t	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
\.


--
-- Data for Name: etl_task; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.etl_task (id, connection_id, destination_table_id, task_type, status, total_blobs, processed_blobs, rows_raw, rows_normalized, rows_destination, current_blob, total_download_bytes, downloaded_bytes_total, current_blob_size, download_bytes, active_downloads, download_blobs, phase, connection_ids, billing_periods, selected_blobs, errors, result_json, started_at, finished_at) FROM stdin;
\.


--
-- Data for Name: forecast_adjustment; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.forecast_adjustment (id, forecast_config_id, name, filters, adjustment_type, percentage, fixed_value, start_period, end_period, priority, active, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: forecast_business_rule; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.forecast_business_rule (id, forecast_config_id, name, identification_column, identification_values, charge_type, behavior, reference_column, reference_values, value_column, start_period, end_period, growth_rate, replacement_value, cancel_on_refund, scope, priority, active, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: forecast_config; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.forecast_config (id, name, description, destination_table_id, measure_column, currency_column, date_column, period_column, type_column, dimensions, cutoff_days, horizon_months, growth_rate, model_mode, enabled_windows, recent_weight, minimum_daily_points, minimum_monthly_points, null_dimension_label, active, version, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: forecast_run; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.forecast_run (id, forecast_config_id, config_version, trigger_type, source_etl_task_id, cutoff_date, status, phase, total_series, processed_series, rows_written, selected_models, error_metrics, result_table, config_snapshot, destination_table_id_snapshot, destination_physical_name, source_snapshot_id, error, started_at, finished_at) FROM stdin;
\.


--
-- Data for Name: ingested_blob; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.ingested_blob (id, connection_id, blob_path, blob_prefix, billing_period, last_modified, etag, content_length, row_count, status, ingested_at) FROM stdin;
\.


--
-- Data for Name: period_status; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.period_status (id, connection_id, billing_period, status, auto_close_days, closed_at, closed_by, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: pivot_view; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.pivot_view (id, name, destination_table_id, config_json, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: project; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.project (id, department_id, projeto_name, cloud, subscription_rg, active, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: scheduled_task; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.scheduled_task (id, name, connection_id, destination_reprocess, cron_expression, timezone, active, last_run_at, next_run_at, config_json, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: skipped_blob; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.skipped_blob (id, connection_id, blob_path, reason, skipped_at) FROM stdin;
\.


--
-- Data for Name: source_connection; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.source_connection (id, code, name, connector_type, cloud_name, config_json, blob_prefixes, created_at, updated_at) FROM stdin;
1	azure-4p	Azure 4P Cost Management	azure_blob	Azure-4P	{"tenant_id": "0fce1554-ffcc-4e69-aa8d-9334eb2d8fd7", "client_id": "b497374e-e461-4043-ac57-406ce004c87d", "client_secret": "lsj8Q~42HyMeNhcX0vtmLx1VWuwr5vRgIiF4XbOA", "storage_account": "proazurecostsmanagement", "container_name": "4pfinops"}	["reports/Report4P-actual-cost/", "reports/Report4P-amortized-cost/"]	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
2	azure-ti	Azure TI Cost Management	azure_blob	Azure-TI	{"tenant_id": "78879b00-f0e9-486c-836f-56f314e47e7f", "client_id": "20fbf218-7baa-417d-af6e-41be4cc81dfc", "client_secret": "VkI8Q~el6n4n4413U.vVIHEbTl3U2rv0wCCfccSw", "storage_account": "reportfinopsti", "container_name": "tifinops"}	["reports/ReportTI-actual-cost/", "reports/ReportTI-amortized-cost/"]	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
3	gcp-4p	GCP 4P Billing Export	gcp_bigquery	GCP-4P	{"project_id": "billing-344820", "dataset_id": "billingVivo", "table_name": "gcp_billing_export_resource_v1_015B69_1CB967_9A157C", "table_name_old": "gcp_billing_export_v1_01804E_42EE23_4CC802"}	[]	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
4	gcp-ti	GCP TI Billing Export	gcp_bigquery	GCP-TI	{"project_id": "vivo-ti-billing", "dataset_id": "ti_bqd_billing", "table_name": "gcp_billing_export_resource_v1_01B23F_E52290_FD9D2C", "table_name_old": "gcp_billing_export_resource_v1_015AC4_5FC0B7_B1B758"}	[]	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
5	mongodb-atlas-pro	MongoDB Atlas Pro	mongodb_atlas	Atlas-MongoDB	{"base_url": "https://cloud.mongodb.com/api/atlas/v1.0", "contract_type": "pro", "group_id": "6349bada4f67da45619e737a", "public_key": "auayvrdi", "private_key": "63a95f7d-6aef-433c-8643-9c00860d6590"}	[]	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
6	mongodb-atlas-enterprise	MongoDB Atlas Enterprise	mongodb_atlas	Atlas-MongoDB	{"base_url": "https://cloud.mongodb.com/api/atlas/v1.0", "contract_type": "enterprise", "group_id": "624c8bef33f07a2b248feab0", "public_key": "cbgqlrlx", "private_key": "8317dc99-bc1a-4d89-8558-a0fc0fba789d"}	[]	2026-08-14 11:36:24.409054+00	2026-08-14 11:36:24.409054+00
\.


--
-- Data for Name: source_destination_mapping; Type: TABLE DATA; Schema: public; Owner: finops
--

COPY public.source_destination_mapping (id, source_connection_id, destination_table_id, name, source_column, destination_column, transform_type, transform_config, priority, active, created_at, updated_at) FROM stdin;
\.


--
-- Name: business_rule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.business_rule_id_seq', 33, true);


--
-- Name: classification_rule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.classification_rule_id_seq', 33, true);


--
-- Name: clone_price_pendency_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.clone_price_pendency_id_seq', 1, false);


--
-- Name: clone_rule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.clone_rule_id_seq', 1, false);


--
-- Name: dashboard_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.dashboard_id_seq', 1, false);


--
-- Name: dashboard_widget_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.dashboard_widget_id_seq', 1, false);


--
-- Name: department_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.department_id_seq', 1, false);


--
-- Name: destination_column_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.destination_column_id_seq', 1, false);


--
-- Name: destination_table_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.destination_table_id_seq', 1, false);


--
-- Name: etl_mapping_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.etl_mapping_id_seq', 33, true);


--
-- Name: etl_task_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.etl_task_id_seq', 1, false);


--
-- Name: forecast_adjustment_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.forecast_adjustment_id_seq', 1, false);


--
-- Name: forecast_business_rule_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.forecast_business_rule_id_seq', 1, false);


--
-- Name: forecast_config_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.forecast_config_id_seq', 1, false);


--
-- Name: forecast_run_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.forecast_run_id_seq', 1, false);


--
-- Name: ingested_blob_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.ingested_blob_id_seq', 1, false);


--
-- Name: period_status_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.period_status_id_seq', 1, false);


--
-- Name: pivot_view_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.pivot_view_id_seq', 1, false);


--
-- Name: project_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.project_id_seq', 1, false);


--
-- Name: scheduled_task_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.scheduled_task_id_seq', 1, false);


--
-- Name: skipped_blob_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.skipped_blob_id_seq', 1, false);


--
-- Name: source_connection_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.source_connection_id_seq', 33, true);


--
-- Name: source_destination_mapping_id_seq; Type: SEQUENCE SET; Schema: public; Owner: finops
--

SELECT pg_catalog.setval('public.source_destination_mapping_id_seq', 1, false);


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
-- Name: clone_price_pendency clone_price_pendency_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_price_pendency
    ADD CONSTRAINT clone_price_pendency_pkey PRIMARY KEY (id);


--
-- Name: clone_rule clone_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_rule
    ADD CONSTRAINT clone_rule_pkey PRIMARY KEY (id);


--
-- Name: dashboard dashboard_name_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard
    ADD CONSTRAINT dashboard_name_key UNIQUE (name);


--
-- Name: dashboard dashboard_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard
    ADD CONSTRAINT dashboard_pkey PRIMARY KEY (id);


--
-- Name: dashboard_widget dashboard_widget_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard_widget
    ADD CONSTRAINT dashboard_widget_pkey PRIMARY KEY (id);


--
-- Name: department department_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.department
    ADD CONSTRAINT department_pkey PRIMARY KEY (id);


--
-- Name: destination_column destination_column_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_column
    ADD CONSTRAINT destination_column_pkey PRIMARY KEY (id);


--
-- Name: destination_table destination_table_name_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_table
    ADD CONSTRAINT destination_table_name_key UNIQUE (name);


--
-- Name: destination_table destination_table_physical_name_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_table
    ADD CONSTRAINT destination_table_physical_name_key UNIQUE (physical_name);


--
-- Name: destination_table destination_table_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_table
    ADD CONSTRAINT destination_table_pkey PRIMARY KEY (id);


--
-- Name: etl_mapping etl_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_mapping
    ADD CONSTRAINT etl_mapping_pkey PRIMARY KEY (id);


--
-- Name: etl_task etl_task_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_task
    ADD CONSTRAINT etl_task_pkey PRIMARY KEY (id);


--
-- Name: forecast_adjustment forecast_adjustment_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_adjustment
    ADD CONSTRAINT forecast_adjustment_pkey PRIMARY KEY (id);


--
-- Name: forecast_business_rule forecast_business_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_business_rule
    ADD CONSTRAINT forecast_business_rule_pkey PRIMARY KEY (id);


--
-- Name: forecast_config forecast_config_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_config
    ADD CONSTRAINT forecast_config_pkey PRIMARY KEY (id);


--
-- Name: forecast_run forecast_run_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_run
    ADD CONSTRAINT forecast_run_pkey PRIMARY KEY (id);


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
-- Name: pivot_view pivot_view_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.pivot_view
    ADD CONSTRAINT pivot_view_pkey PRIMARY KEY (id);


--
-- Name: project project_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.project
    ADD CONSTRAINT project_pkey PRIMARY KEY (id);


--
-- Name: scheduled_task scheduled_task_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.scheduled_task
    ADD CONSTRAINT scheduled_task_pkey PRIMARY KEY (id);


--
-- Name: skipped_blob skipped_blob_connection_id_blob_path_key; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.skipped_blob
    ADD CONSTRAINT skipped_blob_connection_id_blob_path_key UNIQUE (connection_id, blob_path);


--
-- Name: skipped_blob skipped_blob_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.skipped_blob
    ADD CONSTRAINT skipped_blob_pkey PRIMARY KEY (id);


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
-- Name: source_destination_mapping source_destination_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_destination_mapping
    ADD CONSTRAINT source_destination_mapping_pkey PRIMARY KEY (id);


--
-- Name: ix_business_rule_connection_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_business_rule_connection_id ON public.business_rule USING btree (connection_id);


--
-- Name: ix_business_rule_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_business_rule_destination_table_id ON public.business_rule USING btree (destination_table_id);


--
-- Name: ix_class_rule_dep_prio; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_class_rule_dep_prio ON public.classification_rule USING btree (department_id, priority);


--
-- Name: ix_class_rule_dest_prio; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_class_rule_dest_prio ON public.classification_rule USING btree (destination_table_id, priority);


--
-- Name: ix_classification_rule_connection_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_classification_rule_connection_id ON public.classification_rule USING btree (connection_id);


--
-- Name: ix_classification_rule_department_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_classification_rule_department_id ON public.classification_rule USING btree (department_id);


--
-- Name: ix_classification_rule_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_classification_rule_destination_table_id ON public.classification_rule USING btree (destination_table_id);


--
-- Name: ix_classification_rule_project_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_classification_rule_project_id ON public.classification_rule USING btree (project_id);


--
-- Name: ix_clone_price_pendency_clone_rule_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_clone_price_pendency_clone_rule_id ON public.clone_price_pendency USING btree (clone_rule_id);


--
-- Name: ix_clone_price_pendency_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_clone_price_pendency_destination_table_id ON public.clone_price_pendency USING btree (destination_table_id);


--
-- Name: ix_clone_price_pendency_meter_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_clone_price_pendency_meter_id ON public.clone_price_pendency USING btree (meter_id);


--
-- Name: ix_clone_rule_connection_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_clone_rule_connection_id ON public.clone_rule USING btree (connection_id);


--
-- Name: ix_clone_rule_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_clone_rule_destination_table_id ON public.clone_rule USING btree (destination_table_id);


--
-- Name: ix_dashboard_widget_dashboard_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_dashboard_widget_dashboard_id ON public.dashboard_widget USING btree (dashboard_id);


--
-- Name: ix_dashboard_widget_dashboard_position; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_dashboard_widget_dashboard_position ON public.dashboard_widget USING btree (dashboard_id, id);


--
-- Name: ix_department_parent_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_department_parent_id ON public.department USING btree (parent_id);


--
-- Name: ix_dest_col_table_name; Type: INDEX; Schema: public; Owner: finops
--

CREATE UNIQUE INDEX ix_dest_col_table_name ON public.destination_column USING btree (destination_table_id, column_name);


--
-- Name: ix_destination_column_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_destination_column_destination_table_id ON public.destination_column USING btree (destination_table_id);


--
-- Name: ix_etl_mapping_connection_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_etl_mapping_connection_id ON public.etl_mapping USING btree (connection_id);


--
-- Name: ix_forecast_adjustment_forecast_config_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_forecast_adjustment_forecast_config_id ON public.forecast_adjustment USING btree (forecast_config_id);


--
-- Name: ix_forecast_business_rule_forecast_config_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_forecast_business_rule_forecast_config_id ON public.forecast_business_rule USING btree (forecast_config_id);


--
-- Name: ix_forecast_config_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_forecast_config_destination_table_id ON public.forecast_config USING btree (destination_table_id);


--
-- Name: ix_forecast_run_config_status; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_forecast_run_config_status ON public.forecast_run USING btree (forecast_config_id, status);


--
-- Name: ix_forecast_run_destination_table_id_snapshot; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_forecast_run_destination_table_id_snapshot ON public.forecast_run USING btree (destination_table_id_snapshot);


--
-- Name: ix_forecast_run_forecast_config_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_forecast_run_forecast_config_id ON public.forecast_run USING btree (forecast_config_id);


--
-- Name: ix_pivot_view_destination_name; Type: INDEX; Schema: public; Owner: finops
--

CREATE UNIQUE INDEX ix_pivot_view_destination_name ON public.pivot_view USING btree (destination_table_id, name);


--
-- Name: ix_project_department_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_project_department_id ON public.project USING btree (department_id);


--
-- Name: ix_source_destination_mapping_destination_table_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_source_destination_mapping_destination_table_id ON public.source_destination_mapping USING btree (destination_table_id);


--
-- Name: ix_source_destination_mapping_source_connection_id; Type: INDEX; Schema: public; Owner: finops
--

CREATE INDEX ix_source_destination_mapping_source_connection_id ON public.source_destination_mapping USING btree (source_connection_id);


--
-- Name: ix_src_dest_map_unique; Type: INDEX; Schema: public; Owner: finops
--

CREATE UNIQUE INDEX ix_src_dest_map_unique ON public.source_destination_mapping USING btree (source_connection_id, destination_table_id, source_column, destination_column);


--
-- Name: business_rule business_rule_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.business_rule
    ADD CONSTRAINT business_rule_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: business_rule business_rule_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.business_rule
    ADD CONSTRAINT business_rule_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id);


--
-- Name: classification_rule classification_rule_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule
    ADD CONSTRAINT classification_rule_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: classification_rule classification_rule_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule
    ADD CONSTRAINT classification_rule_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.department(id);


--
-- Name: classification_rule classification_rule_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule
    ADD CONSTRAINT classification_rule_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id);


--
-- Name: classification_rule classification_rule_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.classification_rule
    ADD CONSTRAINT classification_rule_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.project(id);


--
-- Name: clone_price_pendency clone_price_pendency_clone_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_price_pendency
    ADD CONSTRAINT clone_price_pendency_clone_rule_id_fkey FOREIGN KEY (clone_rule_id) REFERENCES public.clone_rule(id) ON DELETE SET NULL;


--
-- Name: clone_price_pendency clone_price_pendency_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_price_pendency
    ADD CONSTRAINT clone_price_pendency_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id) ON DELETE SET NULL;


--
-- Name: clone_price_pendency clone_price_pendency_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_price_pendency
    ADD CONSTRAINT clone_price_pendency_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id) ON DELETE CASCADE;


--
-- Name: clone_rule clone_rule_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_rule
    ADD CONSTRAINT clone_rule_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id) ON DELETE CASCADE;


--
-- Name: clone_rule clone_rule_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.clone_rule
    ADD CONSTRAINT clone_rule_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id) ON DELETE CASCADE;


--
-- Name: dashboard dashboard_pivot_view_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard
    ADD CONSTRAINT dashboard_pivot_view_id_fkey FOREIGN KEY (pivot_view_id) REFERENCES public.pivot_view(id) ON DELETE SET NULL;


--
-- Name: dashboard_widget dashboard_widget_dashboard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard_widget
    ADD CONSTRAINT dashboard_widget_dashboard_id_fkey FOREIGN KEY (dashboard_id) REFERENCES public.dashboard(id) ON DELETE CASCADE;


--
-- Name: dashboard_widget dashboard_widget_pivot_view_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.dashboard_widget
    ADD CONSTRAINT dashboard_widget_pivot_view_id_fkey FOREIGN KEY (pivot_view_id) REFERENCES public.pivot_view(id) ON DELETE SET NULL;


--
-- Name: department department_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.department
    ADD CONSTRAINT department_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.department(id);


--
-- Name: destination_column destination_column_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.destination_column
    ADD CONSTRAINT destination_column_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id) ON DELETE CASCADE;


--
-- Name: etl_mapping etl_mapping_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_mapping
    ADD CONSTRAINT etl_mapping_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: etl_task etl_task_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_task
    ADD CONSTRAINT etl_task_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: etl_task etl_task_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.etl_task
    ADD CONSTRAINT etl_task_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id);


--
-- Name: forecast_adjustment forecast_adjustment_forecast_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_adjustment
    ADD CONSTRAINT forecast_adjustment_forecast_config_id_fkey FOREIGN KEY (forecast_config_id) REFERENCES public.forecast_config(id) ON DELETE CASCADE;


--
-- Name: forecast_business_rule forecast_business_rule_forecast_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_business_rule
    ADD CONSTRAINT forecast_business_rule_forecast_config_id_fkey FOREIGN KEY (forecast_config_id) REFERENCES public.forecast_config(id) ON DELETE CASCADE;


--
-- Name: forecast_config forecast_config_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_config
    ADD CONSTRAINT forecast_config_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id) ON DELETE CASCADE;


--
-- Name: forecast_run forecast_run_forecast_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_run
    ADD CONSTRAINT forecast_run_forecast_config_id_fkey FOREIGN KEY (forecast_config_id) REFERENCES public.forecast_config(id) ON DELETE SET NULL;


--
-- Name: forecast_run forecast_run_source_etl_task_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.forecast_run
    ADD CONSTRAINT forecast_run_source_etl_task_id_fkey FOREIGN KEY (source_etl_task_id) REFERENCES public.etl_task(id);


--
-- Name: ingested_blob ingested_blob_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.ingested_blob
    ADD CONSTRAINT ingested_blob_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: period_status period_status_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.period_status
    ADD CONSTRAINT period_status_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: pivot_view pivot_view_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.pivot_view
    ADD CONSTRAINT pivot_view_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id) ON DELETE CASCADE;


--
-- Name: project project_department_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.project
    ADD CONSTRAINT project_department_id_fkey FOREIGN KEY (department_id) REFERENCES public.department(id);


--
-- Name: skipped_blob skipped_blob_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.skipped_blob
    ADD CONSTRAINT skipped_blob_connection_id_fkey FOREIGN KEY (connection_id) REFERENCES public.source_connection(id);


--
-- Name: source_destination_mapping source_destination_mapping_destination_table_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_destination_mapping
    ADD CONSTRAINT source_destination_mapping_destination_table_id_fkey FOREIGN KEY (destination_table_id) REFERENCES public.destination_table(id) ON DELETE CASCADE;


--
-- Name: source_destination_mapping source_destination_mapping_source_connection_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: finops
--

ALTER TABLE ONLY public.source_destination_mapping
    ADD CONSTRAINT source_destination_mapping_source_connection_id_fkey FOREIGN KEY (source_connection_id) REFERENCES public.source_connection(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict wJw5l13uCEhBIcO9Z8NzZFpZpiJQZT0bZYuTn2cgNIAIlLNZ415AS3PKixaVe7e

--
-- Database "postgres" dump
--

--
-- PostgreSQL database dump
--

\restrict IiEsfMZYBHIrQ9W5HGBSoKtbdUaTrvJpSsCa5X5t6d1HAiEQPq2BWk3zEPkZHL8

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

\unrestrict IiEsfMZYBHIrQ9W5HGBSoKtbdUaTrvJpSsCa5X5t6d1HAiEQPq2BWk3zEPkZHL8
\connect postgres
\restrict IiEsfMZYBHIrQ9W5HGBSoKtbdUaTrvJpSsCa5X5t6d1HAiEQPq2BWk3zEPkZHL8

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

\unrestrict IiEsfMZYBHIrQ9W5HGBSoKtbdUaTrvJpSsCa5X5t6d1HAiEQPq2BWk3zEPkZHL8

--
-- PostgreSQL database cluster dump complete
--

