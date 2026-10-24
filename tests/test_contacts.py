from app.extract.contacts import extract_contact_points, registrable_domain
from app.models import ContactType


def kinds(text):
    return {(c.type, c.value) for c in extract_contact_points(text)}


def test_upi_vs_email():
    got = kinds("Pay ₹10,000 to profitwealth@ybl or mail help@sbi.co.in")
    assert (ContactType.UPI, "profitwealth@ybl") in got
    assert (ContactType.EMAIL, "help@sbi.co.in") in got
    assert not any(t == ContactType.UPI and "sbi" in v for t, v in got)


def test_upi_obfuscated_with_spaces_and_valid_handle():
    got = kinds("UPI: profit.wealth @ ybl and zerodha.brk@validhdfc")
    assert (ContactType.UPI, "profit.wealth@ybl") in got
    assert (ContactType.UPI, "zerodha.brk@validhdfc") in got


def test_phones_india():
    got = kinds("Call 98765 43210 or +91-9123456789, bank line 1600123456, toll free 1800-425-3800")
    vals = {v for t, v in got if t == ContactType.PHONE}
    assert {"9876543210", "9123456789", "1600123456", "18004253800"} <= vals


def test_hindi_digits_and_zero_width():
    got = kinds("कॉल करें ९८७६५४३२१० pay​ to pay2me@ybl")
    assert (ContactType.PHONE, "9876543210") in got
    assert (ContactType.UPI, "pay2me@ybl") in got


def test_urls_and_lookalikes():
    got = kinds("Update KYC at sbi-bank.in.co/kyc or https://www.onlinesbi.sbi.bank.in/login")
    urls = {v for t, v in got if t == ContactType.URL}
    assert any("sbi-bank.in.co" in u for u in urls)
    assert any("onlinesbi.sbi.bank.in" in u for u in urls)
    assert registrable_domain("https://www.onlinesbi.sbi.bank.in/login") == ("sbi.bank.in", "www.onlinesbi.sbi.bank.in")
    assert registrable_domain("sbi-bank.in.co/kyc")[0] == "in.co"  # the real owner is in.co, not SBI


def test_amounts_are_not_urls_or_phones():
    got = kinds("Rs.5000 profit in 2.5 days, invest ₹10,000 today")
    assert not got


def test_sms_header_sebi_reg_apk_account():
    got = kinds("VM-HDFCBK-S: Adviser reg INH000012345. Install TradeMax.apk. A/c no: 1234 5678 9012 IFSC HDFC0001234")
    assert (ContactType.SMS_HEADER, "VM-HDFCBK-S") in got
    assert (ContactType.SEBI_REG_NO, "INH000012345") in got
    assert (ContactType.APP, "trademax.apk") in got
    assert (ContactType.BANK_ACCOUNT, "123456789012|HDFC0001234") in got


def upis(text):
    return {v for t, v in kinds(text) if t == ContactType.UPI}


def test_spoken_upi_from_a_transcript():
    # Exactly what Whisper wrote for our lip-synced test fake
    assert upis("My VIP group gives guaranteed returns of 300% in one month. Pay 10,000 to profit wealth at the rate YBL today.") == {"profitwealth@ybl"}
    assert upis("send it to profit dot wealth at the rate of Y B L") == {"profit.wealth@ybl"}
    assert upis("transfer to raju123 at ok axis now") == {"raju123@okaxis"}
    assert upis("our broker id is zerodha dot brk at valid hdfc") == {"zerodha.brk@validhdfc"}


def test_spoken_at_needs_a_real_upi_handle():
    # Everyday sentences with "at" are not UPI IDs
    assert not upis("I have my account at SBI and meet me at the bank at ten")
    assert not upis("We met at Paytm office. Seats are limited at the rate of 50 per day")
    # ...but "at the rate" is the spoken '@', so even a wordy handle counts there
    assert upis("pay ramesh at the rate paytm") == {"ramesh@paytm"}


def test_spoken_and_written_upi_are_one_contact():
    assert upis("Pay profitwealth@ybl, that is profitwealth at the rate YBL") == {"profitwealth@ybl"}


def test_repeated_upi_is_not_read_as_a_url():
    got = kinds("Pay rbi.safe@okaxis now. [UPI QR code: pay to rbi.safe@okaxis (Rahul Kumar)]")
    assert (ContactType.UPI, "rbi.safe@okaxis") in got
    assert not any(t == ContactType.URL for t, _ in got)
