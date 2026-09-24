import qrcode

# 1. ตั้งค่าความละเอียดและขนาด
qr = qrcode.QRCode(
    version=1,  
    error_correction=qrcode.constants.ERROR_CORRECT_H,  
    box_size=10,  
    border=4,  
)

data = "i waa ra u will read"

# 2. เพิ่มข้อมูลลงในตัวตั้งค่าที่เราสร้างไว้
qr.add_data(data)
qr.make(fit=True)

# 3. สร้างเป็นรูปภาพ
img = qr.make_image(fill_color="black", back_color="white")
img.save("qr.png")