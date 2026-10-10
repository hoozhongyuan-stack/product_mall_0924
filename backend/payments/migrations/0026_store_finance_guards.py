from django.db import migrations

SQL=r'''
CREATE FUNCTION store_finance_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'store financial evidence is immutable'; END; $$;
CREATE TRIGGER store_line_finance_immutable BEFORE UPDATE OR DELETE ON payments_storeorderlinefinance FOR EACH ROW EXECUTE FUNCTION store_finance_immutable();
CREATE TRIGGER store_wallet_event_immutable BEFORE UPDATE OR DELETE ON payments_storewalletevent FOR EACH ROW EXECUTE FUNCTION store_finance_immutable();
CREATE TRIGGER store_withdrawal_event_immutable BEFORE UPDATE OR DELETE ON payments_storewithdrawalevent FOR EACH ROW EXECUTE FUNCTION store_finance_immutable();
CREATE FUNCTION store_income_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'store income cannot be deleted'; END IF;
 IF OLD.status='SETTLED' OR NEW.order_id<>OLD.order_id OR NEW.store_id<>OLD.store_id THEN RAISE EXCEPTION 'settled store income is immutable'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER store_income_guard BEFORE UPDATE OR DELETE ON payments_storeincome FOR EACH ROW EXECUTE FUNCTION store_income_guard();
CREATE FUNCTION store_withdrawal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'store withdrawals cannot be deleted'; END IF;
 IF NEW.store_id<>OLD.store_id OR NEW.member_id<>OLD.member_id OR NEW.request_key<>OLD.request_key OR NEW.request_digest<>OLD.request_digest OR NEW.amount_fen<>OLD.amount_fen OR NEW.payee_name<>OLD.payee_name OR NEW.bank_name<>OLD.bank_name OR NEW.encrypted_bank_account<>OLD.encrypted_bank_account OR NEW.bank_account_tail<>OLD.bank_account_tail OR NEW.created_at<>OLD.created_at THEN RAISE EXCEPTION 'withdrawal request evidence is immutable'; END IF;
 IF NOT ((OLD.status='PENDING_REVIEW' AND NEW.status IN ('APPROVED_PENDING_PAYMENT','REJECTED')) OR (OLD.status='APPROVED_PENDING_PAYMENT' AND NEW.status='PAID')) OR NEW.revision<>OLD.revision+1 THEN RAISE EXCEPTION 'invalid store withdrawal transition'; END IF;
 IF NEW.status='PAID' AND (NEW.paid_at IS NULL OR length(trim(NEW.payment_reference))=0) THEN RAISE EXCEPTION 'offline payout requires transfer evidence'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER store_withdrawal_guard BEFORE UPDATE OR DELETE ON payments_storewithdrawal FOR EACH ROW EXECUTE FUNCTION store_withdrawal_guard();
'''
REVERSE=r'''
DROP TRIGGER store_withdrawal_guard ON payments_storewithdrawal;
DROP FUNCTION store_withdrawal_guard();
DROP TRIGGER store_income_guard ON payments_storeincome;
DROP FUNCTION store_income_guard();
DROP TRIGGER store_withdrawal_event_immutable ON payments_storewithdrawalevent;
DROP TRIGGER store_wallet_event_immutable ON payments_storewalletevent;
DROP TRIGGER store_line_finance_immutable ON payments_storeorderlinefinance;
DROP FUNCTION store_finance_immutable();
'''


class Migration(migrations.Migration):
    dependencies=[('payments','0025_settlement_cursor')]
    operations=[migrations.RunSQL(SQL,REVERSE)]
