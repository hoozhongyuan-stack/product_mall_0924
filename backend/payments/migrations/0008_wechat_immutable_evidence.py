from django.db import migrations

FORWARD = """
CREATE FUNCTION wechat_guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'wechat attempt is immutable'; END IF;
 IF ROW(NEW.id,NEW.order_id,NEW.out_trade_no,NEW.app_id,NEW.merchant_id,NEW.payer_openid,NEW.amount_fen,NEW.expires_at,NEW.created_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.order_id,OLD.out_trade_no,OLD.app_id,OLD.merchant_id,OLD.payer_openid,OLD.amount_fen,OLD.expires_at,OLD.created_at)
 OR (OLD.trade_state='SUCCESS' AND NEW.trade_state<>'SUCCESS')
 THEN RAISE EXCEPTION 'wechat payment identity is immutable'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER wechat_attempt_immutable BEFORE UPDATE OR DELETE ON wechat_payment_attempt
FOR EACH ROW EXECUTE FUNCTION wechat_guard_attempt();
CREATE FUNCTION wechat_guard_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'wechat request alias is immutable'; END; $$;
CREATE TRIGGER wechat_request_immutable BEFORE UPDATE OR DELETE ON wechat_prepay_request
FOR EACH ROW EXECUTE FUNCTION wechat_guard_request();
CREATE FUNCTION wechat_guard_operation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'wechat operation is immutable'; END IF;
 IF ROW(NEW.id,NEW.attempt_id,NEW.kind,NEW.started_at) IS DISTINCT FROM ROW(OLD.id,OLD.attempt_id,OLD.kind,OLD.started_at)
 OR (OLD.result<>'STARTED' AND NEW IS DISTINCT FROM OLD)
 THEN RAISE EXCEPTION 'wechat operation evidence is immutable'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER wechat_operation_immutable BEFORE UPDATE OR DELETE ON wechat_payment_operation
FOR EACH ROW EXECUTE FUNCTION wechat_guard_operation();
"""
REVERSE = """
DROP TRIGGER wechat_operation_immutable ON wechat_payment_operation;
DROP FUNCTION wechat_guard_operation();
DROP TRIGGER wechat_request_immutable ON wechat_prepay_request;
DROP FUNCTION wechat_guard_request();
DROP TRIGGER wechat_attempt_immutable ON wechat_payment_attempt;
DROP FUNCTION wechat_guard_attempt();
"""

class Migration(migrations.Migration):
    dependencies = [('payments','0007_wechatpaymentattempt_wechatoperation_and_more')]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
